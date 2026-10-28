"""验证 OAuth 状态绑定、加密存储、重连及邮件缓存的业务边界。"""

import logging
from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.utils import timezone

from email_agent.logging import RedactOAuthQueryFilter
from email_agent.models import EmailMessage, MailboxAccount, OAuthAttempt
from email_agent.providers.base import (
    AccessTokenExpired,
    MailError,
    MailIdentity,
    MessageData,
    MessagePage,
    ReconnectRequired,
)
from email_agent.services.credentials import decrypt_credentials
from email_agent.services.mailboxes import read_message, read_thread, search_messages
from email_agent.services.oauth import begin_connection, disconnect_mailbox, finish_connection

pytestmark = pytest.mark.django_db


@pytest.fixture
def adapter(monkeypatch):
    fake = Mock()
    fake.begin_authorization.side_effect = lambda state: (
        f"https://accounts.google.com/o/oauth2/v2/auth?state={state}",
        {"state": state, "code_verifier": "private-verifier"},
    )
    fake.finish_authorization.return_value = (
        MailIdentity("google-subject", "owner@example.com", "Owner"),
        {"token": "private-access", "refresh_token": "private-refresh"},
    )
    fake.access_token.side_effect = lambda saved, force_refresh=False: ("private-access", saved)
    fake.search.return_value = MessagePage(
        [
            MessageData(
                "message-1",
                thread_id="thread-1",
                subject="邮件主题",
                sender={"address": "alice@example.com"},
            )
        ],
        "provider-page-two",
    )
    fake.get_message.return_value = MessageData(
        "message-1", thread_id="thread-1", subject="邮件主题", body_text="完整正文", attachments=[]
    )
    fake.get_thread.return_value = MessagePage([fake.get_message.return_value])
    monkeypatch.setattr("email_agent.services.oauth.get_provider", lambda name: fake)
    monkeypatch.setattr("email_agent.services.mailboxes.get_provider", lambda name: fake)
    return fake


def begin(owner, **kwargs):
    url = begin_connection(owner, "browser-session", "gmail", "localhost:8000", **kwargs)
    return parse_qs(urlsplit(url).query)["state"][0]


def test_authorization_state_and_credentials_are_encrypted(owner, oauth_settings, adapter):
    state = begin(owner)
    attempt = OAuthAttempt.objects.get(owner=owner)
    assert attempt.state_digest != state
    assert "private-verifier" not in attempt.encrypted_flow
    assert decrypt_credentials(attempt.encrypted_flow)["code_verifier"] == "private-verifier"
    account = finish_connection(
        owner, "browser-session", "gmail", {"state": state, "code": "private-code"}
    )
    assert account.status == MailboxAccount.Status.CONNECTED
    assert "private-access" not in account.encrypted_credentials
    assert decrypt_credentials(account.encrypted_credentials)["refresh_token"] == "private-refresh"
    attempt.refresh_from_db()
    assert attempt.consumed_at
    assert attempt.encrypted_flow == ""
    with pytest.raises(MailError):
        finish_connection(owner, "browser-session", "gmail", {"state": state, "code": "replay"})
    assert adapter.finish_authorization.call_count == 1


@pytest.mark.parametrize(
    "scenario", ["other_session", "other_user", "other_provider", "expired", "cancelled"]
)
def test_invalid_oauth_context_is_rejected_before_exchange(
    owner, oauth_settings, adapter, django_user_model, scenario
):
    state = begin(owner)
    session, provider, user = "browser-session", "gmail", owner
    if scenario == "other_session":
        session = "another-browser"
    elif scenario == "other_user":
        user = django_user_model.objects.create_user(username="other")
    elif scenario == "other_provider":
        provider = "outlook"
    elif scenario == "expired":
        OAuthAttempt.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    else:
        OAuthAttempt.objects.update(cancelled_at=timezone.now())
    with pytest.raises(MailError) as error:
        finish_connection(user, session, provider, {"state": state, "code": "code"})
    assert error.value.code == "invalid_oauth_state"
    adapter.finish_authorization.assert_not_called()


def test_cancelled_consent_consumes_state(owner, oauth_settings, adapter):
    state = begin(owner)
    with pytest.raises(MailError) as error:
        finish_connection(
            owner, "browser-session", "gmail", {"state": state, "error": "access_denied"}
        )
    assert error.value.code == "oauth_cancelled"
    assert OAuthAttempt.objects.get().consumed_at
    adapter.finish_authorization.assert_not_called()


def test_reconnection_cannot_replace_original_mailbox_identity(mailbox, owner, adapter):
    original = mailbox.encrypted_credentials
    state = begin(owner, account_id=mailbox.id)
    adapter.finish_authorization.return_value = (
        MailIdentity("another-subject", "other@example.com"),
        {"token": "another", "refresh_token": "refresh"},
    )
    with pytest.raises(MailError) as error:
        finish_connection(owner, "browser-session", "gmail", {"state": state, "code": "code"})
    mailbox.refresh_from_db()
    assert error.value.code == "identity_mismatch"
    assert mailbox.encrypted_credentials == original


def test_repeated_google_grant_preserves_existing_refresh_token(mailbox, owner, adapter):
    old_refresh = decrypt_credentials(mailbox.encrypted_credentials)["refresh_token"]
    state = begin(owner, account_id=mailbox.id)
    adapter.finish_authorization.return_value = (
        MailIdentity("google-subject", "owner@example.com"),
        {"token": "new-access"},
    )
    account = finish_connection(owner, "browser-session", "gmail", {"state": state, "code": "code"})
    assert account.id == mailbox.id
    assert decrypt_credentials(account.encrypted_credentials)["refresh_token"] == old_refresh
    assert MailboxAccount.objects.count() == 1


def test_search_cache_is_deduplicated_and_metadata_does_not_erase_body(mailbox, owner, adapter):
    found, _ = search_messages(mailbox.id, owner)
    full = read_message(mailbox.id, owner, found[0].id)
    assert full.body_loaded
    assert full.body_text == "完整正文"
    search_messages(mailbox.id, owner)
    full.refresh_from_db()
    assert full.body_loaded and full.body_text == "完整正文"
    assert EmailMessage.objects.count() == 1


def test_signed_cursor_is_bound_to_account_query_and_page_size(mailbox, owner, adapter):
    _, cursor = search_messages(mailbox.id, owner, query="subject:test")
    search_messages(mailbox.id, owner, query="subject:test", cursor=cursor)
    assert adapter.search.call_args.args[-1] == "provider-page-two"
    for parameters in [
        {"query": "changed"},
        {"query": "subject:test", "page_size": 20},
        {"query": "subject:test", "cursor": cursor + "tampered"},
    ]:
        values = {"cursor": cursor, **parameters}
        with pytest.raises(MailError) as error:
            search_messages(mailbox.id, owner, **values)
        assert error.value.code == "invalid_cursor"


def test_authorization_failure_status_is_committed(mailbox, owner, adapter):
    adapter.access_token.side_effect = ReconnectRequired()
    with pytest.raises(ReconnectRequired):
        search_messages(mailbox.id, owner)
    mailbox.refresh_from_db()
    assert mailbox.status == MailboxAccount.Status.RECONNECT_REQUIRED
    assert mailbox.encrypted_credentials == ""


def test_401_refreshes_once_then_retries_read(mailbox, owner, adapter):
    adapter.search.side_effect = [AccessTokenExpired(), MessagePage([])]
    search_messages(mailbox.id, owner)
    assert adapter.search.call_count == 2
    assert adapter.access_token.call_args.kwargs["force_refresh"] is True


def test_repeated_401_does_not_loop(mailbox, owner, adapter):
    adapter.search.side_effect = AccessTokenExpired()
    with pytest.raises(ReconnectRequired):
        search_messages(mailbox.id, owner)
    assert adapter.search.call_count == 2
    mailbox.refresh_from_db()
    assert mailbox.status == MailboxAccount.Status.RECONNECT_REQUIRED


def test_network_failure_preserves_authorization(mailbox, owner, adapter):
    adapter.search.side_effect = MailError("网络不可用", "connection_error")
    with pytest.raises(MailError):
        search_messages(mailbox.id, owner)
    mailbox.refresh_from_db()
    assert mailbox.status == MailboxAccount.Status.CONNECTED
    assert mailbox.encrypted_credentials


def test_disconnect_clears_tokens_but_keeps_cache_and_cancels_handshake(mailbox, owner, adapter):
    results, _ = search_messages(mailbox.id, owner)
    begin(owner, account_id=mailbox.id)
    account = disconnect_mailbox(mailbox.id, owner)
    assert account.encrypted_credentials == ""
    assert account.status == MailboxAccount.Status.DISCONNECTED
    assert EmailMessage.objects.filter(pk=results[0].id).exists()
    assert OAuthAttempt.objects.get().cancelled_at
    with pytest.raises(ReconnectRequired):
        search_messages(mailbox.id, owner)


def test_disconnect_during_token_exchange_rejects_late_callback(mailbox, owner, adapter):
    state = begin(owner, account_id=mailbox.id)

    def late_response(saved, response):
        disconnect_mailbox(mailbox.id, owner)
        return MailIdentity("google-subject", "owner@example.com"), {
            "token": "late-token",
            "refresh_token": "late-refresh",
        }

    adapter.finish_authorization.side_effect = late_response
    with pytest.raises(MailError) as error:
        finish_connection(owner, "browser-session", "gmail", {"state": state, "code": "code"})
    assert error.value.code == "oauth_cancelled"
    mailbox.refresh_from_db()
    assert mailbox.status == MailboxAccount.Status.DISCONNECTED
    assert mailbox.encrypted_credentials == ""


def test_cursor_cannot_be_reused_for_another_owned_mailbox(mailbox, owner, adapter):
    _, cursor = search_messages(mailbox.id, owner)
    another = MailboxAccount.objects.create(
        owner=owner,
        provider="gmail",
        provider_account_id="another-subject",
        email_address="other@example.com",
        encrypted_credentials=mailbox.encrypted_credentials,
    )
    with pytest.raises(MailError) as error:
        search_messages(another.id, owner, cursor=cursor)
    assert error.value.code == "invalid_cursor"


def test_thread_uses_owned_source_message(mailbox, owner, adapter, django_user_model):
    emails, _ = search_messages(mailbox.id, owner)
    other = django_user_model.objects.create_user(username="other")
    with pytest.raises(MailboxAccount.DoesNotExist):
        read_thread(mailbox.id, other, emails[0].id)
    thread, _ = read_thread(mailbox.id, owner, emails[0].id)
    assert thread[0].body_text == "完整正文"


def test_wrong_encryption_key_fails_without_revealing_plaintext(mailbox, settings):
    from cryptography.fernet import Fernet

    settings.MAIL_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
    with pytest.raises(MailError) as error:
        decrypt_credentials(mailbox.encrypted_credentials)
    assert "test-mail-refresh-token" not in str(error.value)


def test_oauth_query_values_are_hidden_from_access_logs():
    record = logging.LogRecord(
        "django.server",
        logging.INFO,
        "",
        1,
        '"%s" %s',
        ("GET /callback/?code=private-code&state=private-state HTTP/1.1", 302),
        None,
    )
    assert RedactOAuthQueryFilter().filter(record)
    assert "private-code" not in record.getMessage()
    assert "private-state" not in record.getMessage()
    assert "302" in record.getMessage()


def test_real_django_server_handler_redacts_oauth_query():
    logger = logging.getLogger("django.server")
    output = StringIO()
    with patch.object(logger.handlers[0], "stream", output):
        logger.info(
            '"%s" %s',
            "GET /callback/?code=real-handler-code&state=real-handler-state HTTP/1.1",
            302,
        )
    assert "real-handler-code" not in output.getvalue()
    assert "real-handler-state" not in output.getvalue()
    assert "已隐藏" in output.getvalue()

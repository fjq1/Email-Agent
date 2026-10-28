"""检验真实服务商请求协议、MIME 清洗与授权 SDK 参数，不访问用户邮箱。"""

import base64
import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from email_agent.providers.base import MailError, ReconnectRequired
from email_agent.providers.config import GMAIL_READ_SCOPE, OUTLOOK_SCOPES, provider_config
from email_agent.providers.gmail import GmailProvider
from email_agent.providers.outlook import OutlookProvider
from email_agent.providers.parsing import gmail_body


def encoded(value):
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode().rstrip("=")


def gmail_message(message_id="m1", body="正文"):
    return {
        "id": message_id,
        "threadId": "thread-1",
        "internalDate": "1700000000000",
        "snippet": "预览 &amp; 信息",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "测试主题"},
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "To", "value": "owner@example.com"},
                {"name": "Date", "value": "Tue, 14 Nov 2023 22:13:20 +0000"},
            ],
            "body": {"data": encoded(body)},
        },
    }


def outlook_message():
    return {
        "id": "immutable-id",
        "conversationId": "conversation-1",
        "subject": "项目进展",
        "from": {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
        "toRecipients": [],
        "receivedDateTime": "2026-10-28T12:00:00Z",
        "bodyPreview": "邮件预览",
        "body": {
            "contentType": "html",
            "content": '<p>正文</p><img src="https://example.com/pixel"><script>bad()</script>',
        },
    }


def test_google_authorization_uses_pkce_and_readonly_scope(oauth_settings):
    url, saved = GmailProvider().begin_authorization("state-for-test")
    params = parse_qs(urlsplit(url).query)
    assert params["state"] == ["state-for-test"]
    assert params["code_challenge_method"] == ["S256"]
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(saved["code_verifier"].encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert params["code_challenge"] == [expected]
    assert GMAIL_READ_SCOPE in params["scope"][0].split()
    assert "gmail.send" not in params["scope"][0]
    assert params["access_type"] == ["offline"]
    assert "test-gmail-secret" not in url


def test_microsoft_uses_official_sdk_and_delegated_read_scopes(oauth_settings):
    app = Mock()
    app.initiate_auth_code_flow.return_value = {
        "auth_uri": "https://login.microsoftonline.com/auth",
        "state": "s",
        "code_verifier": "pkce",
    }
    with patch(
        "email_agent.providers.outlook.msal.ConfidentialClientApplication", return_value=app
    ) as constructor:
        _, flow = OutlookProvider().begin_authorization("s")
    assert constructor.call_args.kwargs["authority"] == "https://login.microsoftonline.com/common"
    assert constructor.call_args.kwargs["client_credential"] == "test-outlook-secret"
    assert app.initiate_auth_code_flow.call_args.args[0] == OUTLOOK_SCOPES
    assert app.initiate_auth_code_flow.call_args.kwargs["response_mode"] == "query"
    assert flow["code_verifier"] == "pkce"


def test_production_rejects_plain_http_callback(oauth_settings, settings):
    settings.DEBUG = False
    with pytest.raises(MailError, match="HTTPS"):
        provider_config("gmail")


def test_gmail_search_uses_native_query_metadata_and_cursor(mail_http):
    captured = []

    def respond(request):
        captured.append(request)
        if request.url.path.endswith("/messages"):
            assert request.url.params["q"] == "from:alice@example.com"
            assert request.url.params["pageToken"] == "page-two"
            return httpx.Response(
                200, json={"messages": [{"id": "m1"}], "nextPageToken": "page-three"}
            )
        assert request.url.params["format"] == "metadata"
        return httpx.Response(200, json=gmail_message())

    mail_http(respond)
    page = GmailProvider().search("token-test", "from:alice@example.com", 10, "page-two")
    assert page.next_cursor == "page-three"
    assert page.messages[0].sender["address"] == "alice@example.com"
    assert page.messages[0].body_text is None
    assert all(item.headers["authorization"] == "Bearer token-test" for item in captured)


def test_gmail_multipart_prefers_plain_and_keeps_attachment_metadata():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/html", "body": {"data": encoded("<p>HTML 版本</p>")}},
            {"mimeType": "text/plain", "body": {"data": encoded("纯文本正文")}},
            {
                "mimeType": "application/pdf",
                "filename": "材料.pdf",
                "body": {"attachmentId": "a1", "size": 42},
            },
        ],
    }
    text, truncated, attachments = gmail_body(payload)
    assert text == "纯文本正文"
    assert not truncated
    assert attachments[0]["name"] == "材料.pdf"


def test_gmail_html_is_plain_text_and_bounded(settings):
    settings.MAIL_MAX_BODY_CHARS = 2
    payload = {
        "mimeType": "text/html",
        "body": {
            "data": encoded(
                '<style>hidden</style><p>可见正文</p><img src="https://external.example"><script>secret()</script>'
            )
        },
    }
    text, truncated, _ = gmail_body(payload)
    assert text == "可见"
    assert truncated


def test_gmail_thread_paginates_full_messages(mail_http):
    mail_http(
        lambda request: httpx.Response(
            200, json={"messages": [gmail_message("m1"), gmail_message("m2"), gmail_message("m3")]}
        )
    )
    provider = GmailProvider()
    first = provider.get_thread("token", "thread-1", 2)
    second = provider.get_thread("token", "thread-1", 2, first.next_cursor)
    assert [item.id for item in first.messages] == ["m1", "m2"]
    assert [item.id for item in second.messages] == ["m3"]
    assert second.messages[0].body_text == "正文"
    assert second.next_cursor == ""


def test_google_refresh_preserves_refresh_token_and_omits_client_secret(
    oauth_settings, monkeypatch
):
    def refreshed(credentials, request):
        credentials.token = "new-access"
        credentials.expiry = datetime(2035, 1, 1)

    monkeypatch.setattr(Credentials, "refresh", refreshed)
    token, saved = GmailProvider().access_token(
        {
            "token": "old",
            "refresh_token": "refresh",
            "expiry": "2000-01-01T00:00:00",
            "scopes": [GMAIL_READ_SCOPE],
        }
    )
    assert token == "new-access"
    assert saved["refresh_token"] == "refresh"
    assert "client_secret" not in saved


def test_google_revoked_refresh_requires_reconnection(oauth_settings, monkeypatch):
    def revoked(*args, **kwargs):
        raise RefreshError("invalid_grant", retryable=False)

    monkeypatch.setattr(Credentials, "refresh", revoked)
    with pytest.raises(ReconnectRequired):
        GmailProvider().access_token(
            {"token": "old", "refresh_token": "revoked", "expiry": "2000-01-01T00:00:00"}
        )


def test_outlook_search_uses_kql_and_immutable_ids(mail_http):
    def respond(request):
        assert request.url.params["$search"] == json.dumps("subject:项目", ensure_ascii=False)
        assert 'IdType="ImmutableId"' in request.headers["prefer"]
        assert "$orderby" not in request.url.params
        return httpx.Response(
            200,
            json={
                "value": [outlook_message()],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?$skip=20",
            },
        )

    mail_http(respond)
    page = OutlookProvider().search("token", "subject:项目", 10)
    assert page.messages[0].id == "immutable-id"
    assert page.messages[0].body_text is None
    assert page.next_cursor.endswith("$skip=20")


def test_outlook_reads_body_and_attachment_metadata(mail_http):
    def respond(request):
        if request.url.path.endswith("/attachments"):
            assert "contentBytes" not in request.url.params["$select"]
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "a1",
                            "name": "报告.pdf",
                            "contentType": "application/pdf",
                            "size": 100,
                        }
                    ]
                },
            )
        data = outlook_message()
        data["hasAttachments"] = True
        return httpx.Response(200, json=data)

    mail_http(respond)
    message = OutlookProvider().get_message("token", "immutable-id")
    assert message.body_text == "正文"
    assert message.attachments[0]["name"] == "报告.pdf"
    assert message.received_at == datetime(2026, 10, 28, 12, tzinfo=UTC)


def test_outlook_accepts_nullable_optional_message_fields(mail_http):
    mail_http(
        lambda request: httpx.Response(
            200,
            json={
                "id": "m1",
                "subject": None,
                "from": None,
                "toRecipients": None,
                "ccRecipients": None,
                "bodyPreview": None,
                "body": {"content": None, "contentType": None},
            },
        )
    )
    message = OutlookProvider().get_message("token", "m1")
    assert message.subject == ""
    assert message.body_text == ""
    assert message.sender["address"] == ""


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/v1.0/me/messages",
        "http://graph.microsoft.com/v1.0/me/messages",
        "https://graph.microsoft.com.evil.example/v1.0/me/messages",
        "https://graph.microsoft.com/v1.0/users/other/messages",
        "https://name:pass@graph.microsoft.com/v1.0/me/messages",
    ],
)
def test_outlook_rejects_unsafe_pagination_urls(url):
    with pytest.raises(MailError):
        OutlookProvider().search("never-leak-this-token", "", 10, url)


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "invalid_query"),
        (401, "access_token_expired"),
        (403, "access_denied"),
        (404, "not_found"),
        (429, "rate_limited"),
        (503, "provider_unavailable"),
    ],
)
def test_provider_failures_are_safe_and_actionable(mail_http, status, code):
    mail_http(lambda request: httpx.Response(status, json={"error": "private-provider-response"}))
    with pytest.raises(MailError) as error:
        GmailProvider().get_message("private-token", "m1")
    assert error.value.code == code
    assert "private" not in str(error.value)


def test_http_response_size_is_limited(mail_http, settings):
    settings.MAIL_MAX_RESPONSE_BYTES = 5
    mail_http(lambda request: httpx.Response(200, json={"body": "oversized"}))
    with pytest.raises(MailError) as error:
        GmailProvider().get_message("token", "m1")
    assert error.value.code == "response_too_large"


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("userRateLimitExceeded", "rate_limited"),
        ("insufficientPermissions", "reconnect_required"),
        ("accessNotConfigured", "api_not_enabled"),
    ],
)
def test_google_403_distinguishes_quota_permissions_and_disabled_api(mail_http, reason, expected):
    mail_http(lambda request: httpx.Response(403, json={"error": {"errors": [{"reason": reason}]}}))
    with pytest.raises(MailError) as error:
        GmailProvider().get_message("private-token", "m1")
    assert error.value.code == expected


def test_microsoft_silent_token_refresh_uses_saved_account(oauth_settings):
    app = Mock()
    app.get_accounts.return_value = [{"home_account_id": "home-id"}]
    app.acquire_token_silent_with_error.return_value = {"access_token": "refreshed-token"}
    with patch.object(OutlookProvider, "_application", return_value=app):
        token, saved = OutlookProvider().access_token(
            {"cache": "{}", "home_account_id": "home-id"}, force_refresh=True
        )
    assert token == "refreshed-token"
    assert saved["home_account_id"] == "home-id"
    assert app.acquire_token_silent_with_error.call_args.kwargs["force_refresh"] is True

"""验证请求返回时的同步结果、登录、跨用户隔离和 CSRF 保护。"""

from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from email_agent.models import AgentRun, EmailMessage, MailboxAccount, OAuthAttempt
from email_agent.providers.base import MailIdentity, MessageData, MessagePage
from email_agent.services.credentials import decrypt_credentials
from email_agent.services.health import infrastructure_status

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checkpoints")]


def test_dashboard_requires_login(client):
    assert client.get(reverse("dashboard")).status_code == 302


def test_request_runs_and_approves_synchronously(client, owner):
    assert client.login(username="owner", password="test-password-only")
    dashboard = client.get(reverse("dashboard"))
    assert dashboard.status_code == 200
    assert "no-store" in dashboard["Cache-Control"]
    assert client.get(reverse("start_probe")).status_code == 405
    assert client.post(reverse("start_probe")).status_code == 302
    run = AgentRun.objects.get(owner=owner)
    assert run.status == AgentRun.Status.WAITING_REVIEW
    response = client.post(
        reverse("review_probe", args=[run.id]), {"decision": "approve"}, follow=True
    )
    run.refresh_from_db()
    assert run.status == AgentRun.Status.SUCCEEDED
    assert "诊断处理完成" in response.content.decode()
    assert run.operations.count() == 1


def test_failure_is_visible_and_manual_retry_finishes_in_request(client, owner):
    client.force_login(owner)
    client.post(reverse("start_probe"))
    run = AgentRun.objects.get(owner=owner)
    with patch("email_agent.workflows.probe.complete_node", side_effect=TimeoutError("模拟超时")):
        response = client.post(
            reverse("review_probe", args=[run.id]), {"decision": "approve"}, follow=True
        )
    run.refresh_from_db()
    assert run.status == AgentRun.Status.FAILED
    assert "诊断处理失败" in response.content.decode()
    assert client.post(reverse("resume_run", args=[run.id])).status_code == 302
    run.refresh_from_db()
    assert run.status == AgentRun.Status.SUCCEEDED
    assert run.operations.count() == 1


def test_resume_without_approval_stays_paused(client, owner):
    client.force_login(owner)
    client.post(reverse("start_probe"))
    run = AgentRun.objects.get(owner=owner)
    client.post(reverse("resume_run", args=[run.id]))
    run.refresh_from_db()
    assert run.status == AgentRun.Status.WAITING_REVIEW
    assert not run.operations.exists()


def test_csrf_is_required(owner):
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    assert client.post(reverse("start_probe")).status_code == 403


def test_other_users_run_is_hidden(client, owner, django_user_model):
    run = AgentRun.objects.create(owner=owner, status=AgentRun.Status.WAITING_REVIEW)
    other = django_user_model.objects.create_user(username="other")
    client.force_login(other)
    assert str(run.id) not in client.get(reverse("dashboard")).content.decode()
    assert (
        client.post(reverse("review_probe", args=[run.id]), {"decision": "approve"}).status_code
        == 404
    )
    assert client.post(reverse("resume_run", args=[run.id])).status_code == 404


def test_health_only_checks_postgresql_and_checkpoints():
    checks = infrastructure_status()
    assert set(checks) == {"postgresql", "checkpoints"}
    assert all(check["ok"] for check in checks.values())


def test_public_health_exposes_no_connection_details(client):
    assert client.get(reverse("liveness")).json() == {"status": "alive"}
    with patch(
        "email_agent.views.infrastructure_status",
        return_value={"postgresql": {"ok": False, "error": "internal"}},
    ):
        response = client.get(reverse("readiness"))
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_mailbox_connect_requires_post_and_csrf(owner, oauth_settings):
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    assert client.get(reverse("mailbox_connect", args=["gmail"])).status_code == 405
    assert (
        client.post(
            reverse("mailbox_connect", args=["gmail"]), HTTP_HOST="localhost:8000"
        ).status_code
        == 403
    )


def test_oauth_browser_flow_uses_same_session_and_cleans_callback_url(
    client, owner, oauth_settings
):
    from unittest.mock import Mock
    from urllib.parse import parse_qs, urlsplit

    client.force_login(owner)
    provider = Mock()
    provider.begin_authorization.side_effect = lambda state: (
        f"https://accounts.google.com/auth?state={state}",
        {"state": state, "code_verifier": "private"},
    )
    provider.finish_authorization.return_value = (
        MailIdentity("sub", "owner@example.com"),
        {"token": "private-access", "refresh_token": "private-refresh"},
    )
    with patch("email_agent.services.oauth.get_provider", return_value=provider):
        response = client.post(
            reverse("mailbox_connect", args=["gmail"]), HTTP_HOST="localhost:8000"
        )
        state = parse_qs(urlsplit(response["Location"]).query)["state"][0]
        response = client.get(
            reverse("mailbox_callback", args=["gmail"]),
            {"state": state, "code": "private-code"},
            HTTP_HOST="localhost:8000",
        )
    assert response.status_code == 302
    assert "private-code" not in response["Location"]
    assert response["Referrer-Policy"] == "no-referrer"
    assert "no-store" in response["Cache-Control"]
    account = MailboxAccount.objects.get(owner=owner)
    assert decrypt_credentials(account.encrypted_credentials)["refresh_token"] == "private-refresh"
    assert OAuthAttempt.objects.get().encrypted_flow == ""


def test_anonymous_oauth_callback_does_not_put_code_in_login_redirect(client):
    response = client.get(
        reverse("mailbox_callback", args=["gmail"]),
        {"code": "private-code", "state": "private-state"},
    )
    assert response["Location"] == reverse("login")
    assert response["Referrer-Policy"] == "no-referrer"


def test_mailbox_pages_isolate_other_users(client, mailbox, owner, django_user_model):
    cached = EmailMessage.objects.create(mailbox=mailbox, provider_message_id="message-1")
    other = django_user_model.objects.create_user(username="mail-other")
    client.force_login(other)
    for name, args in [
        ("mailbox_messages", [mailbox.id]),
        ("mailbox_message", [mailbox.id, cached.id]),
        ("mailbox_thread", [mailbox.id, cached.id]),
    ]:
        assert client.get(reverse(name, args=args)).status_code == 404
    assert client.post(reverse("mailbox_disconnect", args=[mailbox.id])).status_code == 404


def test_mailbox_search_detail_and_thread_escape_untrusted_content(client, mailbox, owner):
    from unittest.mock import Mock

    client.force_login(owner)
    provider = Mock()
    provider.access_token.side_effect = lambda saved, force_refresh=False: ("access-private", saved)
    summary = MessageData(
        "m1",
        thread_id="t1",
        subject='<img src="x" onerror="bad()">',
        sender={"address": "sender@example.com"},
    )
    full = MessageData(
        "m1", thread_id="t1", subject=summary.subject, body_text="<script>bad()</script>"
    )
    provider.search.return_value = MessagePage([summary])
    provider.get_message.return_value = full
    provider.get_thread.return_value = MessagePage([full])
    with patch("email_agent.services.mailboxes.get_provider", return_value=provider):
        response = client.get(
            reverse("mailbox_messages", args=[mailbox.id]), {"query": "subject:test"}
        )
        assert response.status_code == 200
        assert "no-store" in response["Cache-Control"]
        cached = EmailMessage.objects.get(mailbox=mailbox)
        response = client.get(reverse("mailbox_message", args=[mailbox.id, cached.id]))
        content = response.content.decode()
        assert "&lt;script&gt;" in content
        assert "<script>bad()" not in content
        assert "access-private" not in content
        assert (
            client.get(reverse("mailbox_thread", args=[mailbox.id, cached.id])).status_code == 200
        )


def test_invalid_search_form_does_not_call_provider(client, mailbox, owner):
    client.force_login(owner)
    with patch("email_agent.services.mailboxes.get_provider") as provider:
        response = client.get(reverse("mailbox_messages", args=[mailbox.id]), {"page_size": 999})
    assert response.status_code == 200
    provider.assert_not_called()

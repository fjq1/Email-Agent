"""测试使用真实 PostgreSQL 和持久化检查点。"""

import pytest
from cryptography.fernet import Fernet

from email_agent.workflows.checkpoints import initialize_checkpoints


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_user(username="owner", password="test-password-only")


@pytest.fixture
def checkpoints(transactional_db):
    initialize_checkpoints()


@pytest.fixture
def oauth_settings(settings):
    """全部使用测试凭据，避免测试读取或调用用户的真实 OAuth 应用。"""
    settings.DEBUG = True
    settings.MAIL_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
    settings.MAIL_OAUTH_PROVIDERS = {
        name: {
            "client_id": f"test-{name}-client",
            "client_secret": f"test-{name}-secret",
            "redirect_uri": f"http://localhost:8000/mailboxes/oauth/{name}/callback/",
            "tenant": "common",
        }
        for name in ["gmail", "outlook"]
    }


@pytest.fixture
def mailbox(owner, oauth_settings):
    from datetime import timedelta

    from django.utils import timezone

    from email_agent.models import MailboxAccount
    from email_agent.providers.config import GMAIL_SCOPES
    from email_agent.services.credentials import encrypt_credentials

    saved = {
        "token": "test-mail-access-token",
        "refresh_token": "test-mail-refresh-token",
        "expiry": (timezone.now() + timedelta(hours=1)).isoformat(),
        "scopes": GMAIL_SCOPES,
    }
    return MailboxAccount.objects.create(
        owner=owner,
        provider="gmail",
        provider_account_id="google-subject",
        email_address="owner@example.com",
        encrypted_credentials=encrypt_credentials(saved),
    )


@pytest.fixture
def mail_http(monkeypatch):
    """仅模拟邮箱 HTTP 响应，实际执行适配器的 URL、请求头和 JSON 解析代码。"""
    import httpx

    original = httpx.Client

    def install(handler):
        def client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return original(*args, **kwargs)

        monkeypatch.setattr("email_agent.providers.base.httpx.Client", client)

    return install

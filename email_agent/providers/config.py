"""邮箱只读权限和 OAuth 配置校验。"""

import re
from urllib.parse import urlsplit

from django.conf import settings

from .base import MailError

GMAIL_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SCOPES = [GMAIL_READ_SCOPE, "openid", "https://www.googleapis.com/auth/userinfo.email"]
OUTLOOK_SCOPES = ["User.Read", "Mail.Read"]


def provider_config(name):
    config = settings.MAIL_OAUTH_PROVIDERS.get(name)
    if not config or not config["client_id"].strip() or not config["client_secret"].strip():
        raise MailError(
            "请先按照邮箱配置文档填写该平台的 OAuth Client ID 和 Client Secret。",
            "oauth_not_configured",
        )
    callback = urlsplit(config["redirect_uri"])
    local_http = (
        settings.DEBUG
        and callback.scheme == "http"
        and callback.hostname in {"localhost", "127.0.0.1", "::1"}
    )
    if (
        (callback.scheme != "https" and not local_http)
        or not callback.hostname
        or callback.username
        or callback.password
        or callback.query
        or callback.fragment
    ):
        raise MailError(
            "OAuth 回调地址必须使用 HTTPS，本地开发可使用 localhost 的 HTTP 地址。",
            "invalid_redirect_uri",
        )
    if name == "outlook" and not re.fullmatch(
        r"(?:common|consumers|organizations|[0-9a-fA-F-]{36})", config["tenant"]
    ):
        raise MailError(
            "OUTLOOK_TENANT_ID 应填写 common、consumers、organizations 或租户 UUID。",
            "invalid_tenant",
        )
    return config

"""服务商统一数据结构和受限 HTTP 读取，业务层无需处理各平台 JSON 差异。"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

import httpx
from django.conf import settings


class MailError(Exception):
    def __init__(self, message, code="mail_error"):
        super().__init__(message)
        self.code = code


class ReconnectRequired(MailError):
    def __init__(self):
        super().__init__("邮箱授权已失效或权限不足，请重新连接。", "reconnect_required")


class AccessTokenExpired(MailError):
    def __init__(self):
        super().__init__("访问令牌已失效。", "access_token_expired")


@dataclass
class MailIdentity:
    account_id: str
    email: str
    display_name: str = ""


@dataclass
class MessageData:
    id: str
    thread_id: str = ""
    internet_message_id: str = ""
    subject: str = ""
    sender: dict = field(default_factory=dict)
    to_recipients: list = field(default_factory=list)
    cc_recipients: list = field(default_factory=list)
    received_at: datetime | None = None
    sent_at: datetime | None = None
    snippet: str = ""
    # None 代表本次只读取摘要，不能用空字符串覆盖之前已读取的正文。
    body_text: str | None = None
    body_truncated: bool = False
    attachments: list | None = None


@dataclass
class MessagePage:
    messages: list[MessageData]
    next_cursor: str = ""


class MailProvider:
    allowed_hosts = set()

    def get_json(self, token, url, params=None):
        try:
            target = urlsplit(url)
            valid = (
                target.scheme == "https"
                and target.hostname in self.allowed_hosts
                and not target.username
                and not target.password
                and target.port in (None, 443)
                and not target.fragment
            )
        except ValueError:
            valid = False
        if not valid:
            raise MailError("邮箱接口返回了无效的请求地址，请重新检索。", "unsafe_provider_url")
        headers = {"Authorization": f"Bearer {token}"}
        headers.update(self.request_headers())
        try:
            # 不自动跟随跳转，避免将授权头发送到重定向后的其他站点。
            with httpx.Client(timeout=settings.MAIL_HTTP_TIMEOUT, follow_redirects=False) as client:
                with client.stream("GET", url, params=params, headers=headers) as response:
                    if response.status_code == 401:
                        raise AccessTokenExpired()
                    if response.status_code == 403:
                        self._forbidden(response)
                    if response.status_code == 429:
                        raise MailError("邮箱服务暂时限制了请求频率，请稍后重试。", "rate_limited")
                    if response.status_code == 404:
                        raise MailError("邮件或会话已不存在，请刷新列表。", "not_found")
                    if response.status_code == 400:
                        raise MailError(
                            "邮箱服务无法识别检索条件，请检查该平台的检索语法。", "invalid_query"
                        )
                    if not 200 <= response.status_code < 300:
                        raise MailError(
                            "邮箱服务暂时无法完成请求，请稍后重试。", "provider_unavailable"
                        )
                    raw = self._response_bytes(response)
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError
            return data
        except httpx.RequestError:
            raise MailError(
                "暂时无法连接邮箱服务，请检查网络后重试。", "connection_error"
            ) from None
        except (ValueError, UnicodeError):
            raise MailError(
                "邮箱服务返回了无法解析的数据，请稍后重试。", "invalid_response"
            ) from None

    def request_headers(self):
        return {}

    @staticmethod
    def _response_bytes(response):
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > settings.MAIL_MAX_RESPONSE_BYTES:
                raise MailError(
                    "邮件响应过大，请缩小检索范围或在原邮箱查看。", "response_too_large"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _forbidden(self, response):
        # Gmail 配额耗尽也可能返回 403；此时不能误删仍有效的刷新令牌。
        try:
            error = json.loads(self._response_bytes(response)).get("error", {})
            reasons = {str(error.get("code", "")).lower(), str(error.get("status", "")).lower()}
            reasons.update(str(item.get("reason", "")).lower() for item in error.get("errors", []))
        except (ValueError, TypeError, AttributeError):
            reasons = set()
        if reasons & {
            "ratelimitexceeded",
            "userratelimitexceeded",
            "dailylimitexceeded",
            "quotaexceeded",
            "resource_exhausted",
        }:
            raise MailError("邮箱服务暂时限制了请求频率或配额，请稍后重试。", "rate_limited")
        if reasons & {
            "insufficientpermissions",
            "insufficient_scope",
            "erroraccessdenied",
            "authorization_requestdenied",
        }:
            raise ReconnectRequired()
        if reasons & {"accessnotconfigured", "servicedisabled"}:
            raise MailError("请先在 Google Cloud 项目中启用 Gmail API。", "api_not_enabled")
        raise MailError(
            "邮箱服务拒绝访问，请检查账户权限、邮箱开通情况或组织策略。", "access_denied"
        )

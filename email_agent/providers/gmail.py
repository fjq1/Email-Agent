"""Gmail 官方 OAuth 与只读 REST API，授权与读取共享一套账户身份。"""

import secrets
from datetime import UTC
from urllib.parse import quote

from django.conf import settings
from django.utils.dateparse import parse_datetime
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from .base import MailError, MailIdentity, MailProvider, MessageData, MessagePage, ReconnectRequired
from .config import GMAIL_READ_SCOPE, GMAIL_SCOPES, provider_config
from .parsing import (
    addresses,
    decoded_header,
    gmail_body,
    html_to_text,
    message_date,
    milliseconds_date,
)

API = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailProvider(MailProvider):
    allowed_hosts = {"gmail.googleapis.com", "openidconnect.googleapis.com"}

    def _flow(self, state, verifier):
        config = provider_config("gmail")
        # 授权地址和令牌地址固定为 Google 官方地址，不接受用户提供的网络地址。
        client = {
            "web": {
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        return Flow.from_client_config(
            client,
            scopes=GMAIL_SCOPES,
            redirect_uri=config["redirect_uri"],
            state=state,
            code_verifier=verifier,
            autogenerate_code_verifier=False,
        )

    def begin_authorization(self, state):
        verifier = secrets.token_urlsafe(64)
        flow = self._flow(state, verifier)
        url, _ = flow.authorization_url(access_type="offline", prompt="consent select_account")
        return url, {"state": state, "code_verifier": verifier}

    def finish_authorization(self, saved, response):
        if not response.get("code"):
            raise MailError("授权响应缺少授权码，请重新连接。", "missing_code")
        flow = self._flow(saved["state"], saved["code_verifier"])
        try:
            flow.fetch_token(code=response["code"], timeout=settings.MAIL_HTTP_TIMEOUT)
            credentials = flow.credentials
        except Exception:
            # SDK 的异常可能包含授权码和令牌响应，不能直接展示或记录异常原文。
            raise MailError(
                "Google 授权码交换失败，请检查凭据、回调地址或重新授权。", "oauth_exchange_failed"
            ) from None
        scopes = (
            credentials.granted_scopes
            if credentials.granted_scopes is not None
            else credentials.scopes
        )
        if GMAIL_READ_SCOPE not in (scopes or []):
            raise ReconnectRequired()
        user = self.get_json(credentials.token, "https://openidconnect.googleapis.com/v1/userinfo")
        profile = self.get_json(credentials.token, f"{API}/profile")
        if not user.get("sub") or not profile.get("emailAddress"):
            raise MailError("Google 未返回可识别的邮箱身份。", "invalid_identity")
        # 使用官方 UserInfo 的稳定 sub，不以可变邮箱地址作为账户唯一身份。
        identity = MailIdentity(user["sub"], profile["emailAddress"], user.get("name", ""))
        return identity, self._credential_data(credentials)

    @staticmethod
    def _credential_data(credentials):
        return {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
            "scopes": list(credentials.granted_scopes or credentials.scopes or GMAIL_SCOPES),
        }

    def access_token(self, saved, force_refresh=False):
        config = provider_config("gmail")
        if not saved.get("refresh_token"):
            raise ReconnectRequired()
        expiry = parse_datetime(saved["expiry"]) if saved.get("expiry") else None
        if expiry and expiry.tzinfo:
            expiry = expiry.astimezone(UTC).replace(tzinfo=None)
        credentials = Credentials(
            token=saved.get("token"),
            refresh_token=saved["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=config["client_id"],
            client_secret=config["client_secret"],
            scopes=saved.get("scopes", GMAIL_SCOPES),
            expiry=expiry,
        )
        if force_refresh or not credentials.valid:
            transport = Request()
            try:

                def bounded_request(*args, **kwargs):
                    kwargs["timeout"] = settings.MAIL_HTTP_TIMEOUT
                    return transport(*args, **kwargs)

                credentials.refresh(bounded_request)
            except TransportError:
                raise MailError(
                    "刷新 Google 授权时网络不可用，请稍后重试。", "connection_error"
                ) from None
            except RefreshError as exc:
                if getattr(exc, "retryable", False):
                    raise MailError(
                        "Google 暂时无法刷新授权，请稍后重试。", "provider_unavailable"
                    ) from None
                raise ReconnectRequired() from None
            finally:
                transport.session.close()
        return credentials.token, self._credential_data(credentials)

    def search(self, token, query, page_size, cursor=""):
        params = {"maxResults": page_size}
        if query:
            params["q"] = query
        if cursor:
            params["pageToken"] = cursor
        page = self.get_json(token, f"{API}/messages", params)
        messages = []
        for item in page.get("messages", []):
            data = self.get_json(
                token,
                f"{API}/messages/{quote(item['id'], safe='')}",
                {
                    "format": "metadata",
                    "metadataHeaders": ["Subject", "From", "To", "Cc", "Date", "Message-ID"],
                },
            )
            messages.append(self._message(data, full=False))
        return MessagePage(messages, page.get("nextPageToken", ""))

    def get_message(self, token, message_id):
        data = self.get_json(
            token, f"{API}/messages/{quote(message_id, safe='')}", {"format": "full"}
        )
        return self._message(data, full=True)

    def get_thread(self, token, thread_id, page_size, cursor=""):
        data = self.get_json(
            token, f"{API}/threads/{quote(thread_id, safe='')}", {"format": "full"}
        )
        try:
            offset = int(cursor or 0)
            if offset < 0:
                raise ValueError
        except ValueError:
            raise MailError("会话分页已失效，请从第一页重新查看。", "invalid_cursor") from None
        # Gmail 会话接口一次返回整个会话；这里只解析当前页，统一为分页展示。
        messages = data.get("messages", [])
        next_offset = offset + page_size
        return MessagePage(
            [self._message(item, full=True) for item in messages[offset:next_offset]],
            str(next_offset) if next_offset < len(messages) else "",
        )

    def _message(self, data, full):
        if not data.get("id"):
            raise MailError("Google 返回的邮件缺少 ID。", "invalid_response")
        payload = data.get("payload", {})
        headers = {
            item["name"].lower(): item.get("value", "") for item in payload.get("headers", [])
        }
        sender = addresses(headers.get("from"))
        message = MessageData(
            id=data["id"],
            thread_id=data.get("threadId", ""),
            internet_message_id=headers.get("message-id", ""),
            subject=decoded_header(headers.get("subject", "")),
            sender=sender[0] if sender else {},
            to_recipients=addresses(headers.get("to")),
            cc_recipients=addresses(headers.get("cc")),
            received_at=milliseconds_date(data.get("internalDate")),
            sent_at=message_date(headers.get("date")),
            snippet=html_to_text(data.get("snippet", "")),
        )
        if full:
            message.body_text, message.body_truncated, message.attachments = gmail_body(payload)
        return message

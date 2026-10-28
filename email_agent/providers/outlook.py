"""Microsoft 官方 MSAL 授权和 Graph 邮件读取，兼容个人及工作/学校账户。"""

import json
from urllib.parse import quote, urlsplit

import msal
from django.conf import settings

from .base import MailError, MailIdentity, MailProvider, MessageData, MessagePage, ReconnectRequired
from .config import OUTLOOK_SCOPES, provider_config
from .parsing import bounded_text, html_to_text, message_date

API = "https://graph.microsoft.com/v1.0"
SUMMARY_FIELDS = "id,conversationId,internetMessageId,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,bodyPreview,hasAttachments"


class OutlookProvider(MailProvider):
    allowed_hosts = {"graph.microsoft.com"}

    def request_headers(self):
        return {"Prefer": 'IdType="ImmutableId", outlook.body-content-type="text"'}

    def _application(self, cache=None):
        config = provider_config("outlook")
        return msal.ConfidentialClientApplication(
            config["client_id"],
            client_credential=config["client_secret"],
            authority=f"https://login.microsoftonline.com/{config['tenant']}",
            token_cache=cache,
            timeout=settings.MAIL_HTTP_TIMEOUT,
            instance_discovery=False,
            enable_pii_log=False,
        )

    def begin_authorization(self, state):
        config = provider_config("outlook")
        try:
            flow = self._application().initiate_auth_code_flow(
                OUTLOOK_SCOPES,
                redirect_uri=config["redirect_uri"],
                state=state,
                prompt="select_account",
                response_mode="query",
            )
        except Exception:
            raise MailError(
                "暂时无法建立 Microsoft 授权连接，请检查网络和应用配置。", "oauth_start_failed"
            ) from None
        if not flow.get("auth_uri"):
            raise MailError("Microsoft 未返回授权地址，请检查应用配置。", "oauth_start_failed")
        # MSAL 自动加入 PKCE、nonce 和离线访问权限，完整 flow 需要加密保存。
        return flow["auth_uri"], flow

    def finish_authorization(self, saved, response):
        cache = msal.SerializableTokenCache()
        try:
            app = self._application(cache)
            result = app.acquire_token_by_auth_code_flow(saved, response)
        except Exception:
            raise MailError(
                "Microsoft 授权校验失败，请重新连接。", "oauth_exchange_failed"
            ) from None
        if not result.get("access_token"):
            raise MailError(
                "Microsoft 未完成授权，请检查应用权限和账户类型后重新连接。",
                "oauth_exchange_failed",
            )
        scopes = {scope.lower() for scope in result.get("scope", "").split()}
        if "mail.read" not in scopes and "https://graph.microsoft.com/mail.read" not in scopes:
            raise ReconnectRequired()
        profile = self.get_json(
            result["access_token"],
            f"{API}/me",
            {"$select": "id,mail,userPrincipalName,displayName"},
        )
        accounts = app.get_accounts()
        if not accounts or not profile.get("id"):
            raise MailError("Microsoft 未返回可恢复的账户身份，请重新连接。", "invalid_identity")
        home_id = accounts[0]["home_account_id"]
        identity = MailIdentity(
            home_id,
            profile.get("mail") or profile.get("userPrincipalName", ""),
            profile.get("displayName", ""),
        )
        return identity, {"cache": cache.serialize(), "home_account_id": home_id}

    def access_token(self, saved, force_refresh=False):
        try:
            cache = msal.SerializableTokenCache()
            cache.deserialize(saved["cache"])
            app = self._application(cache)
            account = next(
                (
                    item
                    for item in app.get_accounts()
                    if item["home_account_id"] == saved["home_account_id"]
                ),
                None,
            )
            if account is None:
                raise ReconnectRequired()
            result = app.acquire_token_silent_with_error(
                OUTLOOK_SCOPES, account=account, force_refresh=force_refresh
            )
        except MailError:
            raise
        except (KeyError, ValueError):
            raise ReconnectRequired() from None
        except Exception:
            raise MailError(
                "刷新 Microsoft 授权时网络不可用，请稍后重试。", "connection_error"
            ) from None
        if not result or not result.get("access_token"):
            if result and result.get("error") in {"temporarily_unavailable", "server_error"}:
                raise MailError("Microsoft 暂时无法刷新授权，请稍后重试。", "provider_unavailable")
            raise ReconnectRequired()
        return result["access_token"], {
            "cache": cache.serialize(),
            "home_account_id": saved["home_account_id"],
        }

    @staticmethod
    def _next_url(cursor):
        parsed = urlsplit(cursor)
        # nextLink 必须原样使用，但携带授权头前先限定官方域名和邮件资源路径。
        if (
            parsed.scheme != "https"
            or parsed.netloc != "graph.microsoft.com"
            or parsed.path != "/v1.0/me/messages"
            or parsed.fragment
        ):
            raise MailError(
                "Microsoft 返回了无效的分页地址，请从第一页重新检索。", "invalid_cursor"
            )
        return cursor

    def search(self, token, query, page_size, cursor=""):
        if cursor:
            page = self.get_json(token, self._next_url(cursor))
        else:
            params = {"$select": SUMMARY_FIELDS, "$top": page_size}
            if query:
                params["$search"] = json.dumps(query, ensure_ascii=False)
            else:
                params["$orderby"] = "receivedDateTime desc"
            page = self.get_json(token, f"{API}/me/messages", params)
        return MessagePage(
            [self._message(item, full=False) for item in page.get("value", [])],
            page.get("@odata.nextLink", ""),
        )

    def get_message(self, token, message_id):
        endpoint = f"{API}/me/messages/{quote(message_id, safe='')}"
        data = self.get_json(token, endpoint, {"$select": f"{SUMMARY_FIELDS},body"})
        message = self._message(data, full=True)
        if data.get("hasAttachments"):
            attachments = self.get_json(
                token,
                f"{endpoint}/attachments",
                {"$select": "id,name,contentType,size,isInline", "$top": 100},
            )
            message.attachments = [
                {
                    "id": item["id"],
                    "name": item.get("name", ""),
                    "content_type": item.get("contentType", ""),
                    "size": item.get("size", 0),
                }
                for item in attachments.get("value", [])
            ]
            if attachments.get("@odata.nextLink"):
                message.attachments.append({"name": "更多附件请在原邮箱查看", "size": None})
        else:
            message.attachments = []
        return message

    def get_thread(self, token, thread_id, page_size, cursor=""):
        if cursor:
            page = self.get_json(token, self._next_url(cursor))
        else:
            # 不混用 $orderby，避免 Graph 对 filter/orderby 组合的限制。
            params = {
                "$filter": f"conversationId eq '{thread_id.replace(chr(39), chr(39) * 2)}'",
                "$select": f"{SUMMARY_FIELDS},body",
                "$top": page_size,
            }
            page = self.get_json(token, f"{API}/me/messages", params)
        return MessagePage(
            [self._message(item, full=True) for item in page.get("value", [])],
            page.get("@odata.nextLink", ""),
        )

    @staticmethod
    def _message(data, full):
        if not data.get("id"):
            raise MailError("Microsoft 返回的邮件缺少 ID。", "invalid_response")

        def address(item):
            source = (item or {}).get("emailAddress") or {}
            return {"name": source.get("name") or "", "address": source.get("address") or ""}

        message = MessageData(
            id=data["id"],
            thread_id=data.get("conversationId") or "",
            internet_message_id=data.get("internetMessageId") or "",
            subject=data.get("subject") or "",
            sender=address(data.get("from") or {}),
            to_recipients=[address(item) for item in data.get("toRecipients") or []],
            cc_recipients=[address(item) for item in data.get("ccRecipients") or []],
            received_at=message_date(data.get("receivedDateTime")),
            sent_at=message_date(data.get("sentDateTime")),
            snippet=data.get("bodyPreview") or "",
        )
        if full:
            body = data.get("body") or {}
            text = body.get("content") or ""
            if (body.get("contentType") or "").lower() == "html":
                text = html_to_text(text)
            message.body_text, message.body_truncated = bounded_text(text)
        return message

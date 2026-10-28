"""一次性 OAuth 握手：把浏览器、登录用户和授权流程绑定到一起。"""

import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from email_agent.models import MailboxAccount, OAuthAttempt
from email_agent.providers import get_provider
from email_agent.providers.base import MailError
from email_agent.providers.config import provider_config
from email_agent.services.credentials import (
    credential_cipher,
    decrypt_credentials,
    encrypt_credentials,
)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def connection_options():
    """只返回页面展示所需配置状态；密钥和客户端凭据不进入模板。"""
    options = []
    for name, label in MailboxAccount.Provider.choices:
        config = settings.MAIL_OAUTH_PROVIDERS[name]
        options.append(
            {
                "name": name,
                "label": label,
                "configured": bool(config["client_id"] and config["client_secret"]),
                "redirect_uri": config["redirect_uri"],
            }
        )
    return options


def begin_connection(owner, session_key, provider_name, current_host, account_id=None):
    config = provider_config(provider_name)
    credential_cipher()
    if urlsplit(config["redirect_uri"]).netloc.lower() != current_host.lower():
        raise MailError(
            "请从回调地址对应的主机打开本应用并登录，再连接邮箱；localhost 与 127.0.0.1 的登录会话分开。",
            "callback_host_mismatch",
        )
    target = None
    if account_id:
        target = MailboxAccount.objects.get(pk=account_id, owner=owner, provider=provider_name)
    state = secrets.token_urlsafe(32)
    url, saved = get_provider(provider_name).begin_authorization(state)
    expected_host = (
        "accounts.google.com" if provider_name == "gmail" else "login.microsoftonline.com"
    )
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != expected_host:
        raise MailError("邮箱平台返回了无效授权地址，请检查配置。", "invalid_authorization_url")
    now = timezone.now()
    # 按需清理该用户的过期临时记录，不依赖后台定时任务。
    OAuthAttempt.objects.filter(owner=owner, expires_at__lt=now - timedelta(days=1)).delete()
    OAuthAttempt.objects.create(
        owner=owner,
        provider=provider_name,
        state_digest=digest(state),
        session_digest=digest(session_key),
        encrypted_flow=encrypt_credentials(saved),
        target_account=target,
        expires_at=now + timedelta(minutes=10),
    )
    return url


def finish_connection(owner, session_key, provider_name, response):
    state = response.get("state", "")
    if not state or len(state) > 512:
        raise MailError("授权状态无效，请重新连接邮箱。", "invalid_oauth_state")
    with transaction.atomic():
        attempt = (
            OAuthAttempt.objects.select_for_update()
            .filter(
                owner=owner,
                provider=provider_name,
                state_digest=digest(state),
                session_digest=digest(session_key),
            )
            .first()
        )
        if (
            not attempt
            or attempt.consumed_at
            or attempt.cancelled_at
            or attempt.expires_at <= timezone.now()
        ):
            raise MailError("授权已过期、已取消或已经使用，请重新连接。", "invalid_oauth_state")
        encrypted_flow = attempt.encrypted_flow
        attempt.consumed_at = timezone.now()
        attempt.encrypted_flow = ""
        attempt.save(update_fields=["consumed_at", "encrypted_flow"])
    # 先提交“一次性消费”再访问网络，防止相同回调并发兑换授权码。
    if response.get("error"):
        raise MailError("邮箱授权未完成或被取消，可以重新连接。", "oauth_cancelled")
    identity, credentials = get_provider(provider_name).finish_authorization(
        decrypt_credentials(encrypted_flow), response
    )
    try:
        validate_email(identity.email)
    except ValidationError:
        raise MailError(
            "服务商未返回有效邮箱地址，请检查账户是否已开通邮箱。", "invalid_identity"
        ) from None
    if not identity.account_id or len(identity.account_id) > 320:
        raise MailError("服务商未返回有效账户身份。", "invalid_identity")
    with transaction.atomic():
        # 与断开连接保持相同锁顺序：先账户、后授权过程，避免并发操作相互等待。
        if attempt.target_account_id:
            target = MailboxAccount.objects.select_for_update().get(
                pk=attempt.target_account_id, owner=owner
            )
            if target.provider_account_id != identity.account_id:
                raise MailError(
                    "重新授权时选择了不同邮箱，请选择原邮箱，或使用“连接新邮箱”。",
                    "identity_mismatch",
                )
        account, _ = MailboxAccount.objects.get_or_create(
            owner=owner,
            provider=provider_name,
            provider_account_id=identity.account_id,
            defaults={"email_address": identity.email},
        )
        account = MailboxAccount.objects.select_for_update().get(pk=account.pk)
        # 如果授权期间用户断开连接，应尊重断开操作，不接受迟到的网络响应。
        if OAuthAttempt.objects.select_for_update().get(pk=attempt.pk).cancelled_at:
            raise MailError("这次授权已取消，请重新连接。", "oauth_cancelled")
        # Google 某些重复授权响应可能不再返回 refresh_token；只复用同一身份的旧令牌。
        if provider_name == "gmail" and not credentials.get("refresh_token"):
            if account.encrypted_credentials:
                credentials["refresh_token"] = decrypt_credentials(
                    account.encrypted_credentials
                ).get("refresh_token")
            if not credentials.get("refresh_token"):
                raise MailError(
                    "Google 未授予离线访问，请重新连接并确认授权。", "offline_access_missing"
                )
        account.email_address = identity.email
        account.display_name = (identity.display_name or "")[:255]
        account.encrypted_credentials = encrypt_credentials(credentials)
        account.status = MailboxAccount.Status.CONNECTED
        account.connected_at = timezone.now()
        account.last_error_code = ""
        account.save()
    return account


def disconnect_mailbox(account_id, owner):
    with transaction.atomic():
        account = MailboxAccount.objects.select_for_update().get(pk=account_id, owner=owner)
        account.encrypted_credentials = ""
        account.status = MailboxAccount.Status.DISCONNECTED
        account.last_error_code = ""
        account.save(
            update_fields=["encrypted_credentials", "status", "last_error_code", "updated_at"]
        )
        attempts = list(
            OAuthAttempt.objects.select_for_update()
            .filter(owner=owner, provider=account.provider)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        OAuthAttempt.objects.filter(pk__in=attempts).update(
            cancelled_at=timezone.now(), encrypted_flow=""
        )
    # 此操作只清除本应用的本地凭据；服务商侧授权可在 Google/Microsoft 账户中撤销。
    return account

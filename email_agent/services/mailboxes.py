"""只读邮件业务服务：串行刷新凭据、限制重试、校验分页、幂等缓存。"""

from django.core import signing
from django.db import transaction
from django.utils import timezone

from email_agent.models import EmailMessage, MailboxAccount
from email_agent.providers import get_provider
from email_agent.providers.base import AccessTokenExpired, MailError, ReconnectRequired
from email_agent.services.credentials import decrypt_credentials, encrypt_credentials

CURSOR_SALT = "mailbox-pagination-v1"


def _token(account_id, owner, force_refresh=False, *, allow_send=False):
    failure = None
    with transaction.atomic():
        account = MailboxAccount.objects.select_for_update().get(pk=account_id, owner=owner)
        if account.status != MailboxAccount.Status.CONNECTED or not account.encrypted_credentials:
            raise ReconnectRequired()
        saved = decrypt_credentials(account.encrypted_credentials)
        try:
            options = {"force_refresh": force_refresh}
            if allow_send:
                if not account.can_send:
                    raise MailError("请先为该邮箱启用发送权限。", "send_permission_required")
                options["allow_send"] = True
            token, updated = get_provider(account.provider).access_token(saved, **options)
        except ReconnectRequired as exc:
            failure = exc
            account.status = MailboxAccount.Status.RECONNECT_REQUIRED
            account.can_send = False
            account.encrypted_credentials = ""
            account.last_error_code = exc.code
            account.save(
                update_fields=[
                    "status",
                    "can_send",
                    "encrypted_credentials",
                    "last_error_code",
                    "updated_at",
                ]
            )
        else:
            if updated != saved:
                account.encrypted_credentials = encrypt_credentials(updated)
                account.save(update_fields=["encrypted_credentials", "updated_at"])
    # 必须在事务提交后抛出异常，否则“需要重新授权”状态会被回滚。
    if failure:
        raise failure
    return token


def _read(account, owner, operation, *arguments):
    provider = get_provider(account.provider)
    try:
        token = _token(account.id, owner)
        try:
            result = getattr(provider, operation)(token, *arguments)
        except AccessTokenExpired:
            # 只对 401 强制刷新一次；普通网络错误由用户显式重试。
            token = _token(account.id, owner, force_refresh=True)
            try:
                result = getattr(provider, operation)(token, *arguments)
            except AccessTokenExpired:
                raise ReconnectRequired() from None
    except ReconnectRequired as exc:
        MailboxAccount.objects.filter(
            pk=account.id, owner=owner, status=MailboxAccount.Status.CONNECTED
        ).update(
            status=MailboxAccount.Status.RECONNECT_REQUIRED,
            encrypted_credentials="",
            can_send=False,
            last_error_code=exc.code,
        )
        raise
    except MailError as exc:
        MailboxAccount.objects.filter(pk=account.id, owner=owner).update(last_error_code=exc.code)
        raise
    MailboxAccount.objects.filter(pk=account.id, owner=owner).update(
        last_checked_at=timezone.now(), last_error_code=""
    )
    return result


def _cursor_context(account, owner, kind, query, page_size):
    return {
        "mailbox": str(account.id),
        "owner": str(owner.pk),
        "kind": kind,
        "query": query,
        "size": page_size,
    }


def _decode_cursor(cursor, context):
    if not cursor:
        return ""
    try:
        saved = signing.loads(cursor, salt=CURSOR_SALT, max_age=900)
        if (
            not isinstance(saved, dict)
            or any(saved.get(key) != value for key, value in context.items())
            or not isinstance(saved.get("next"), str)
        ):
            raise ValueError
        return saved["next"]
    except (signing.BadSignature, ValueError, TypeError):
        raise MailError(
            "分页链接已过期或与当前检索条件不符，请从第一页重新查看。", "invalid_cursor"
        ) from None


def _encode_cursor(value, context):
    return (
        signing.dumps({**context, "next": value}, salt=CURSOR_SALT, compress=True) if value else ""
    )


def _store_messages(account, owner, data):
    stored, seen = [], set()
    with transaction.atomic():
        current = MailboxAccount.objects.select_for_update().get(pk=account.id, owner=owner)
        if current.status != MailboxAccount.Status.CONNECTED:
            raise ReconnectRequired()
        for item in data:
            if not item.id or len(item.id) > 1024 or len(item.thread_id) > 1024:
                raise MailError("服务商返回了无效邮件身份。", "invalid_response")
            if item.id in seen:
                continue
            seen.add(item.id)
            values = {
                "provider_thread_id": item.thread_id,
                "internet_message_id": item.internet_message_id[:1024],
                "subject": item.subject[:998],
                "sender": item.sender,
                "to_recipients": item.to_recipients,
                "cc_recipients": item.cc_recipients,
                "reply_to": item.reply_to,
                "references": item.references,
                "received_at": item.received_at,
                "sent_at": item.sent_at,
                "snippet": item.snippet,
            }
            if item.body_text is not None:
                values.update(
                    body_text=item.body_text,
                    body_loaded=True,
                    body_truncated=item.body_truncated,
                    body_fetched_at=timezone.now(),
                )
            if item.attachments is not None:
                values["attachments"] = item.attachments
            message, _ = EmailMessage.objects.update_or_create(
                mailbox=current, provider_message_id=item.id, defaults=values
            )
            stored.append(message)
    return stored


def search_messages(account_id, owner, query="", page_size=10, cursor=""):
    account = MailboxAccount.objects.get(pk=account_id, owner=owner)
    if not 1 <= page_size <= 20 or len(query) > 500:
        raise MailError("请使用最多 500 字符的查询，每页最多 20 封邮件。", "invalid_query")
    context = _cursor_context(account, owner, "search", query, page_size)
    page = _read(account, owner, "search", query, page_size, _decode_cursor(cursor, context))
    return _store_messages(account, owner, page.messages), _encode_cursor(page.next_cursor, context)


def read_message(account_id, owner, message_id):
    account = MailboxAccount.objects.get(pk=account_id, owner=owner)
    message = EmailMessage.objects.get(pk=message_id, mailbox=account)
    data = _read(account, owner, "get_message", message.provider_message_id)
    return _store_messages(account, owner, [data])[0]


def read_thread(account_id, owner, message_id, page_size=10, cursor=""):
    account = MailboxAccount.objects.get(pk=account_id, owner=owner)
    message = EmailMessage.objects.get(pk=message_id, mailbox=account)
    if not message.provider_thread_id:
        raise MailError("该邮件没有可用的会话标识。", "thread_unavailable")
    if not 1 <= page_size <= 20:
        raise MailError("每页最多读取 20 封会话邮件。", "invalid_query")
    context = _cursor_context(account, owner, "thread", message.provider_thread_id, page_size)
    page = _read(
        account,
        owner,
        "get_thread",
        message.provider_thread_id,
        page_size,
        _decode_cursor(cursor, context),
    )
    return _store_messages(account, owner, page.messages), _encode_cursor(page.next_cursor, context)

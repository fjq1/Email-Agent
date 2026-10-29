"""真实邮箱账户、一次性授权过程和按需缓存的邮件。"""

import uuid

from django.conf import settings
from django.db import models

from .operations import TimestampedModel


class MailboxAccount(TimestampedModel):
    class Provider(models.TextChoices):
        GMAIL = "gmail", "Gmail"
        OUTLOOK = "outlook", "Outlook"

    class Status(models.TextChoices):
        CONNECTED = "connected", "已连接"
        RECONNECT_REQUIRED = "reconnect_required", "需要重新授权"
        DISCONNECTED = "disconnected", "已断开"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="mailboxes"
    )
    provider = models.CharField("平台", max_length=16, choices=Provider)
    provider_account_id = models.CharField("平台账户身份", max_length=320)
    email_address = models.EmailField("邮箱地址", max_length=320)
    display_name = models.CharField("显示名称", max_length=255, blank=True)
    status = models.CharField("连接状态", max_length=24, choices=Status, default=Status.CONNECTED)
    # 此字段只保存 Fernet 密文；客户端密钥始终由 .env 提供，不重复存入令牌数据。
    encrypted_credentials = models.TextField("加密授权凭据", blank=True)
    connected_at = models.DateTimeField("最近授权时间", null=True, blank=True)
    last_checked_at = models.DateTimeField("最近访问时间", null=True, blank=True)
    last_error_code = models.CharField("最近错误类型", max_length=80, blank=True)
    can_send = models.BooleanField("已授予发送权限", default=False)

    class Meta:
        ordering = ["email_address"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "provider", "provider_account_id"],
                name="unique_owned_mailbox_identity",
            )
        ]
        verbose_name = "邮箱账户"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.get_provider_display()} / {self.email_address}"


class OAuthAttempt(models.Model):
    """绑定登录用户和浏览器会话，授权状态十分钟内有效且只能使用一次。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    provider = models.CharField(max_length=16, choices=MailboxAccount.Provider)
    state_digest = models.CharField(max_length=64, unique=True)
    session_digest = models.CharField(max_length=64)
    encrypted_flow = models.TextField()
    target_account = models.ForeignKey(
        MailboxAccount, on_delete=models.CASCADE, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)


class EmailMessage(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mailbox = models.ForeignKey(MailboxAccount, on_delete=models.CASCADE, related_name="emails")
    # 服务商 ID 区分大小写；Outlook 统一使用 ImmutableId，避免文件夹移动改变身份。
    provider_message_id = models.CharField("平台邮件 ID", max_length=1024)
    provider_thread_id = models.CharField("平台会话 ID", max_length=1024, blank=True, db_index=True)
    internet_message_id = models.CharField("邮件 Message-ID", max_length=1024, blank=True)
    subject = models.CharField("主题", max_length=998, blank=True)
    sender = models.JSONField("发件人", default=dict)
    to_recipients = models.JSONField("收件人", default=list)
    cc_recipients = models.JSONField("抄送", default=list)
    reply_to = models.JSONField("回复地址", default=list)
    references = models.TextField("邮件引用链", blank=True)
    received_at = models.DateTimeField("收到时间", null=True, blank=True)
    sent_at = models.DateTimeField("发送时间", null=True, blank=True)
    snippet = models.TextField("正文预览", blank=True)
    body_text = models.TextField("纯文本正文", blank=True)
    body_loaded = models.BooleanField("正文已读取", default=False)
    is_read = models.BooleanField("是否已读", null=True, blank=True)
    body_truncated = models.BooleanField("正文被截断", default=False)
    body_fetched_at = models.DateTimeField("正文读取时间", null=True, blank=True)
    attachments = models.JSONField("附件元信息", default=list)

    class Meta:
        ordering = ["-received_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["mailbox", "provider_message_id"], name="unique_mailbox_message"
            )
        ]
        indexes = [models.Index(fields=["mailbox", "provider_thread_id"])]
        verbose_name = "邮件缓存"
        verbose_name_plural = verbose_name

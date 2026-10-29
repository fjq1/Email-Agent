"""自然语言指令的可见计划、每封邮件进度，以及用户明确确认的处理偏好。"""

import uuid

from django.conf import settings
from django.db import models

from .analysis import AnalysisResult
from .mailboxes import EmailMessage, MailboxAccount
from .operations import AgentRun, TimestampedModel
from .reviews import ActionProposal


class UserPreference(TimestampedModel):
    class Key(models.TextChoices):
        TIMEZONE = "timezone_name", "默认时区"
        LANGUAGE = "reply_language", "回复语言"
        TONE = "reply_tone", "回复风格"
        SIGNATURE = "signature", "邮件签名"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    mailbox = models.ForeignKey(MailboxAccount, on_delete=models.PROTECT, null=True, blank=True)
    key = models.CharField("偏好类型", max_length=30, choices=Key)
    value = models.CharField("已确认的值", max_length=500)
    enabled = models.BooleanField("启用", default=True)
    version = models.PositiveIntegerField(default=1)
    confirmed_at = models.DateTimeField()
    confirmation_source = models.CharField("确认来源", max_length=120)

    @property
    def value_label(self):
        labels = {
            "reply_language": {"auto": "跟随来信语言", "zh": "中文", "en": "英文"},
            "reply_tone": {"professional": "正式、专业", "friendly": "友好", "concise": "简洁"},
        }
        return labels.get(self.key, {}).get(self.value, self.value)

    class Meta:
        ordering = ["key", "-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "key"],
                condition=models.Q(mailbox__isnull=True),
                name="unique_global_preference",
            ),
            models.UniqueConstraint(
                fields=["owner", "mailbox", "key"],
                condition=models.Q(mailbox__isnull=False),
                name="unique_mailbox_preference",
            ),
        ]


class CommandPlan(TimestampedModel):
    class Status(models.TextChoices):
        PREVIEW = "preview", "待核对计划"
        SEARCHED = "searched", "已检索"
        PROCESSING = "processing", "正在逐批分析"
        READY = "ready", "待审核处理动作"
        DISPATCHING = "dispatching", "正在执行已确认动作"
        COMPLETED = "completed", "已完成"
        PARTIAL = "partial", "部分未完成"
        FAILED = "failed", "处理失败"
        CANCELLED = "cancelled", "已取消后续处理"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.OneToOneField(AgentRun, on_delete=models.PROTECT, related_name="command")
    mailbox = models.ForeignKey(MailboxAccount, on_delete=models.PROTECT)
    submission_key = models.UUIDField(unique=True)
    instruction = models.TextField("用户原始指令")
    timezone_name = models.CharField(max_length=80)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status, default=Status.PREVIEW)
    spec = models.JSONField("已校验的意图和检索范围", default=dict)
    preference_snapshot = models.JSONField("本计划采用的已确认偏好", default=dict)
    provider = models.CharField(max_length=16)
    model_name = models.CharField(max_length=200)
    usage = models.JSONField(default=dict)
    native_query = models.TextField(blank=True)
    scanned = models.PositiveIntegerField(default=0)
    has_more = models.BooleanField(default=False)
    searched_at = models.DateTimeField(null=True, blank=True)
    selection_locked_at = models.DateTimeField(null=True, blank=True)
    # 批量确认前保存具体动作版本，重试不能扩大收件人或悄悄换成新内容。
    approved_manifest = models.JSONField(default=list)
    error_message = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at"]


class CommandItem(TimestampedModel):
    class Status(models.TextChoices):
        FOUND = "found", "待选择处理"
        DONE = "done", "已分析"
        FAILED = "failed", "分析未完成"
        SKIPPED = "skipped", "未选择"

    plan = models.ForeignKey(CommandPlan, on_delete=models.CASCADE, related_name="items")
    message = models.ForeignKey(EmailMessage, on_delete=models.PROTECT)
    analysis = models.ForeignKey(AnalysisResult, on_delete=models.PROTECT, null=True, blank=True)
    selected = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status, default=Status.FOUND)
    error_message = models.CharField(max_length=400, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "message"], name="unique_command_message")
        ]


class CommandAction(models.Model):
    plan = models.ForeignKey(CommandPlan, on_delete=models.CASCADE, related_name="actions")
    item = models.ForeignKey(CommandItem, on_delete=models.CASCADE)
    proposal = models.ForeignKey(ActionProposal, on_delete=models.PROTECT)
    proposal_version = models.PositiveIntegerField()
    content_hash = models.CharField(max_length=64)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    error_message = models.CharField(max_length=400, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "proposal"], name="unique_command_proposal")
        ]

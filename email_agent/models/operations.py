"""运行、幂等操作和可靠投递所需的基础模型。"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        abstract = True


class AgentRun(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "等待执行"
        RUNNING = "running", "执行中"
        WAITING_REVIEW = "waiting_review", "等待审核"
        SUCCEEDED = "succeeded", "已完成"
        REJECTED = "rejected", "已拒绝"
        FAILED = "failed", "执行失败"

    class Decision(models.TextChoices):
        APPROVE = "approve", "确认"
        REJECT = "reject", "拒绝"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name="所属用户"
    )
    kind = models.CharField("流程类型", max_length=40, default="infrastructure_probe")
    # 每次业务运行有独立的图线程，和邮箱中的邮件会话 ID 分开。
    thread_id = models.UUIDField("图线程 ID", default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(
        "状态", max_length=24, choices=Status, default=Status.PENDING, db_index=True
    )
    review_decision = models.CharField("审核决定", max_length=16, choices=Decision, blank=True)
    result = models.JSONField("处理结果", default=dict, blank=True)
    last_error_code = models.CharField("最近错误类型", max_length=120, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "工作流运行"
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["owner", "-created_at"])]

    def __str__(self):
        return f"{self.id} / {self.get_status_display()}"


class OperationRecord(TimestampedModel):
    class Status(models.TextChoices):
        SUCCEEDED = "succeeded", "成功"
        FAILED = "failed", "失败"
        UNKNOWN = "unknown", "结果未知"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AgentRun, on_delete=models.PROTECT, related_name="operations")
    idempotency_key = models.CharField("幂等键", max_length=160, unique=True)
    operation_type = models.CharField("操作类型", max_length=40)
    status = models.CharField("结果", max_length=16, choices=Status)
    result = models.JSONField("结果摘要", default=dict)

    class Meta:
        verbose_name = "操作结果"
        verbose_name_plural = verbose_name


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AgentRun, on_delete=models.PROTECT, related_name="audit_events")
    event_key = models.CharField("事件唯一键", max_length=180, unique=True)
    event_type = models.CharField("事件类型", max_length=60)
    metadata = models.JSONField("脱敏附加信息", default=dict, blank=True)
    created_at = models.DateTimeField("记录时间", auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "审计事件"
        verbose_name_plural = verbose_name


class OutboxEvent(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AgentRun, on_delete=models.PROTECT, related_name="outbox_events")
    event_type = models.CharField("事件类型", max_length=40, default="advance_probe")
    available_at = models.DateTimeField("最早投递时间", default=timezone.now, db_index=True)
    published_at = models.DateTimeField("队列已接收时间", null=True, blank=True, db_index=True)
    claim_token = models.UUIDField("领取标识", null=True, blank=True)
    lease_until = models.DateTimeField("领取到期时间", null=True, blank=True)
    attempts = models.PositiveIntegerField("投递尝试次数", default=0)
    last_error_code = models.CharField("最近错误类型", max_length=120, blank=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "待投递事件"
        verbose_name_plural = verbose_name

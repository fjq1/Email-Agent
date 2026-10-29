"""真实动作的人工审核、执行账本与内部待办，和模型分析结果分开保存。"""

import uuid

from django.conf import settings
from django.db import models

from .analysis import AnalysisResult, TaskCandidate
from .operations import AgentRun, TimestampedModel


class ActionProposal(TimestampedModel):
    class Kind(models.TextChoices):
        TASK = "create_task", "创建待办"
        REPLY = "send_reply", "发送回复"

    class ReviewState(models.TextChoices):
        PENDING = "pending", "待审核"
        APPROVED = "approved", "已确认"
        REJECTED = "rejected", "已拒绝"
        CANCELLED = "cancelled", "已取消"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analysis = models.ForeignKey(AnalysisResult, on_delete=models.PROTECT, related_name="proposals")
    candidate = models.ForeignKey(TaskCandidate, on_delete=models.PROTECT, null=True, blank=True)
    run = models.OneToOneField(AgentRun, on_delete=models.PROTECT, related_name="proposal")
    kind = models.CharField("动作", max_length=24, choices=Kind)
    # 同一邮件只维护一份回复审核单，同一去重候选只维护一份建任务审核单。
    source_key = models.CharField("业务唯一键", max_length=100, unique=True)
    version = models.PositiveIntegerField("内容版本", default=1)
    content = models.JSONField("当前已保存内容")
    content_hash = models.CharField("内容摘要", max_length=64)
    source_digest = models.CharField("发送前核对的来源摘要", max_length=64, blank=True)
    source_snapshot = models.JSONField("本版本审核时的来源快照", default=dict)
    review_state = models.CharField(
        "审核状态", max_length=16, choices=ReviewState, default=ReviewState.PENDING
    )

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "动作审核单"
        verbose_name_plural = verbose_name


class ApprovalDecision(models.Model):
    """不可变的审核凭据：保存用户实际批准的版本、摘要和内容。"""

    proposal = models.ForeignKey(ActionProposal, on_delete=models.PROTECT, related_name="decisions")
    run = models.ForeignKey(AgentRun, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    version = models.PositiveIntegerField()
    decision = models.CharField(max_length=16, choices=ActionProposal.ReviewState)
    content_hash = models.CharField(max_length=64)
    source_digest = models.CharField(max_length=64, blank=True)
    snapshot = models.JSONField()
    source_snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["proposal", "version"], name="unique_proposal_version_decision"
            )
        ]


class ActionExecution(TimestampedModel):
    class Status(models.TextChoices):
        EXECUTING = "executing", "执行中"
        SUCCEEDED = "succeeded", "已完成"
        ACCEPTED = "accepted", "邮箱服务已受理"
        FAILED = "failed", "失败，可重试"
        UNKNOWN = "unknown", "结果待核对，禁止重发"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    proposal = models.ForeignKey(
        ActionProposal, on_delete=models.PROTECT, related_name="executions"
    )
    approval = models.OneToOneField(ApprovalDecision, on_delete=models.PROTECT)
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status)
    result = models.JSONField(default=dict, blank=True)
    last_error_code = models.CharField(max_length=100, blank=True)
    error_message = models.CharField(max_length=400, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["proposal", "version"], name="unique_proposal_version_execution"
            )
        ]


class ActionAttempt(models.Model):
    execution = models.ForeignKey(
        ActionExecution, on_delete=models.PROTECT, related_name="attempts"
    )
    number = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=ActionExecution.Status)
    error_code = models.CharField(max_length=100, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(fields=["execution", "number"], name="unique_execution_attempt")
        ]


class TaskItem(TimestampedModel):
    class Status(models.TextChoices):
        TODO = "todo", "待办"
        DOING = "doing", "进行中"
        BLOCKED = "blocked", "已阻塞"
        DONE = "done", "已完成"
        CANCELLED = "cancelled", "已取消"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    candidate = models.OneToOneField(TaskCandidate, on_delete=models.PROTECT, related_name="task")
    execution = models.OneToOneField(ActionExecution, on_delete=models.PROTECT, related_name="task")
    title = models.CharField("标题", max_length=300)
    description = models.TextField("说明", blank=True)
    responsible_name = models.CharField("负责人称呼", max_length=200, blank=True)
    responsible_email = models.EmailField("负责人邮箱", max_length=320, blank=True)
    due_date = models.DateField("截止日期", null=True, blank=True)
    due_time = models.TimeField("截止时间", null=True, blank=True)
    timezone_name = models.CharField("日期时区", max_length=80)
    dependency_notes = models.TextField("尚未关联的依赖说明", blank=True)
    dependencies = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="dependents"
    )
    status = models.CharField("状态", max_length=16, choices=Status, default=Status.TODO)
    version = models.PositiveIntegerField("待办版本", default=1)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "正式待办"
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.title

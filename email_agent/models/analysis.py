"""邮件理解结果与任务候选；正式待办和邮件发送由独立的人工审核模块负责。"""

import uuid

from django.db import models

from .mailboxes import EmailMessage, MailboxAccount
from .operations import AgentRun, TimestampedModel


class AnalysisResult(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(EmailMessage, on_delete=models.PROTECT, related_name="analyses")
    run = models.OneToOneField(AgentRun, on_delete=models.PROTECT, related_name="analysis")
    # 同一输入、提示词版本、模型和时区复用结果，重复点击不再次计费。
    input_key = models.CharField("分析输入指纹", max_length=64, unique=True)
    provider = models.CharField("模型平台", max_length=16)
    model_name = models.CharField("模型名称", max_length=200)
    prompt_version = models.CharField("提示词版本", max_length=40)
    timezone_name = models.CharField("日期解释时区", max_length=80)
    source_snapshot = models.JSONField("分析时的邮件原文快照")
    payload = models.JSONField("已校验的结构化结果", default=dict, blank=True)
    usage = models.JSONField("模型用量", default=dict, blank=True)
    error_message = models.CharField("可公开的错误说明", max_length=400, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "邮件分析"
        verbose_name_plural = verbose_name

    @property
    def category_label(self):
        return {
            "notification": "通知",
            "reply_required": "需要回复",
            "task_required": "需要执行任务",
        }.get(self.payload.get("primary_category"), "尚无结果")


class TaskCandidate(TimestampedModel):
    """多封邮件可以指向同一候选事项，冲突保留在各来源中供后续审核。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mailbox = models.ForeignKey(MailboxAccount, on_delete=models.PROTECT, related_name="candidates")
    dedupe_key = models.CharField("事项指纹", max_length=64, unique=True)
    title = models.CharField("事项", max_length=300)
    # 仅保存首个来源的展示信息；后续改期、负责人冲突不得悄悄覆盖它。
    details = models.JSONField("初始候选信息")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "待审核任务候选"
        verbose_name_plural = verbose_name


class TaskSource(models.Model):
    candidate = models.ForeignKey(TaskCandidate, on_delete=models.CASCADE, related_name="sources")
    analysis = models.ForeignKey(
        AnalysisResult, on_delete=models.CASCADE, related_name="task_sources"
    )
    item_index = models.PositiveSmallIntegerField("原分析中的事项序号")
    details = models.JSONField("本来源的候选信息与证据")
    merged = models.BooleanField("复用已有候选", default=False)
    conflict = models.BooleanField("与已有候选信息存在差异", default=False)
    # 模糊相似只提示，绝不据此自动合并。
    possible_duplicates = models.JSONField("可能重复的候选 ID", default=list, blank=True)

    class Meta:
        ordering = ["item_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["analysis", "item_index"], name="unique_analysis_task_source"
            )
        ]
        verbose_name = "候选事项来源"
        verbose_name_plural = verbose_name

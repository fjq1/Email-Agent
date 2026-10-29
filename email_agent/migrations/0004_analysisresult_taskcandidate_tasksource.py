# 阶段 2：增加分析结果、去重候选和带证据的事项来源。

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("email_agent", "0003_mailbox_connections"),
    ]

    operations = [
        migrations.CreateModel(
            name="AnalysisResult",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "input_key",
                    models.CharField(max_length=64, unique=True, verbose_name="分析输入指纹"),
                ),
                ("provider", models.CharField(max_length=16, verbose_name="模型平台")),
                ("model_name", models.CharField(max_length=200, verbose_name="模型名称")),
                ("prompt_version", models.CharField(max_length=40, verbose_name="提示词版本")),
                ("timezone_name", models.CharField(max_length=80, verbose_name="日期解释时区")),
                ("source_snapshot", models.JSONField(verbose_name="分析时的邮件原文快照")),
                (
                    "payload",
                    models.JSONField(blank=True, default=dict, verbose_name="已校验的结构化结果"),
                ),
                ("usage", models.JSONField(blank=True, default=dict, verbose_name="模型用量")),
                (
                    "error_message",
                    models.CharField(blank=True, max_length=400, verbose_name="可公开的错误说明"),
                ),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="analyses",
                        to="email_agent.emailmessage",
                    ),
                ),
                (
                    "run",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="analysis",
                        to="email_agent.agentrun",
                    ),
                ),
            ],
            options={
                "verbose_name": "邮件分析",
                "verbose_name_plural": "邮件分析",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="TaskCandidate",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "dedupe_key",
                    models.CharField(max_length=64, unique=True, verbose_name="事项指纹"),
                ),
                ("title", models.CharField(max_length=300, verbose_name="事项")),
                ("details", models.JSONField(verbose_name="初始候选信息")),
                (
                    "mailbox",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="candidates",
                        to="email_agent.mailboxaccount",
                    ),
                ),
            ],
            options={
                "verbose_name": "待审核任务候选",
                "verbose_name_plural": "待审核任务候选",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="TaskSource",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("item_index", models.PositiveSmallIntegerField(verbose_name="原分析中的事项序号")),
                ("details", models.JSONField(verbose_name="本来源的候选信息与证据")),
                ("merged", models.BooleanField(default=False, verbose_name="复用已有候选")),
                (
                    "conflict",
                    models.BooleanField(default=False, verbose_name="与已有候选信息存在差异"),
                ),
                (
                    "possible_duplicates",
                    models.JSONField(blank=True, default=list, verbose_name="可能重复的候选 ID"),
                ),
                (
                    "analysis",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="task_sources",
                        to="email_agent.analysisresult",
                    ),
                ),
                (
                    "candidate",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sources",
                        to="email_agent.taskcandidate",
                    ),
                ),
            ],
            options={
                "verbose_name": "候选事项来源",
                "verbose_name_plural": "候选事项来源",
                "ordering": ["item_index"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("analysis", "item_index"), name="unique_analysis_task_source"
                    )
                ],
            },
        ),
    ]

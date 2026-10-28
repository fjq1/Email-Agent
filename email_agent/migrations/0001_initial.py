# 由 Django 自动生成的阶段 0 基础设施迁移。

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentRun",
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
                    "kind",
                    models.CharField(
                        default="infrastructure_probe", max_length=40, verbose_name="流程类型"
                    ),
                ),
                (
                    "thread_id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, unique=True, verbose_name="图线程 ID"
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "等待执行"),
                            ("running", "执行中"),
                            ("waiting_review", "等待审核"),
                            ("succeeded", "已完成"),
                            ("rejected", "已拒绝"),
                            ("failed", "执行失败"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=24,
                        verbose_name="状态",
                    ),
                ),
                (
                    "review_decision",
                    models.CharField(
                        blank=True,
                        choices=[("approve", "确认"), ("reject", "拒绝")],
                        max_length=16,
                        verbose_name="审核决定",
                    ),
                ),
                ("result", models.JSONField(blank=True, default=dict, verbose_name="处理结果")),
                (
                    "last_error_code",
                    models.CharField(blank=True, max_length=120, verbose_name="最近错误类型"),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="所属用户",
                    ),
                ),
            ],
            options={
                "verbose_name": "工作流运行",
                "verbose_name_plural": "工作流运行",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="AuditEvent",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "event_key",
                    models.CharField(max_length=180, unique=True, verbose_name="事件唯一键"),
                ),
                ("event_type", models.CharField(max_length=60, verbose_name="事件类型")),
                (
                    "metadata",
                    models.JSONField(blank=True, default=dict, verbose_name="脱敏附加信息"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="记录时间")),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="audit_events",
                        to="email_agent.agentrun",
                    ),
                ),
            ],
            options={
                "verbose_name": "审计事件",
                "verbose_name_plural": "审计事件",
                "ordering": ["created_at"],
            },
        ),
        migrations.CreateModel(
            name="OperationRecord",
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
                    "idempotency_key",
                    models.CharField(max_length=160, unique=True, verbose_name="幂等键"),
                ),
                ("operation_type", models.CharField(max_length=40, verbose_name="操作类型")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("succeeded", "成功"),
                            ("failed", "失败"),
                            ("unknown", "结果未知"),
                        ],
                        max_length=16,
                        verbose_name="结果",
                    ),
                ),
                ("result", models.JSONField(default=dict, verbose_name="结果摘要")),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="operations",
                        to="email_agent.agentrun",
                    ),
                ),
            ],
            options={
                "verbose_name": "操作结果",
                "verbose_name_plural": "操作结果",
            },
        ),
        migrations.CreateModel(
            name="OutboxEvent",
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
                    "event_type",
                    models.CharField(
                        default="advance_probe", max_length=40, verbose_name="事件类型"
                    ),
                ),
                (
                    "available_at",
                    models.DateTimeField(
                        db_index=True,
                        default=django.utils.timezone.now,
                        verbose_name="最早投递时间",
                    ),
                ),
                (
                    "published_at",
                    models.DateTimeField(
                        blank=True, db_index=True, null=True, verbose_name="队列已接收时间"
                    ),
                ),
                ("claim_token", models.UUIDField(blank=True, null=True, verbose_name="领取标识")),
                (
                    "lease_until",
                    models.DateTimeField(blank=True, null=True, verbose_name="领取到期时间"),
                ),
                ("attempts", models.PositiveIntegerField(default=0, verbose_name="投递尝试次数")),
                (
                    "last_error_code",
                    models.CharField(blank=True, max_length=120, verbose_name="最近错误类型"),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="outbox_events",
                        to="email_agent.agentrun",
                    ),
                ),
            ],
            options={
                "verbose_name": "待投递事件",
                "verbose_name_plural": "待投递事件",
                "ordering": ["created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="agentrun",
            index=models.Index(
                fields=["owner", "-created_at"], name="email_agent_owner_i_d012b2_idx"
            ),
        ),
    ]

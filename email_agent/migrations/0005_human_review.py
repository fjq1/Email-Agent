# 阶段 3：发送能力、回复地址、人工审批凭据、执行记录和正式待办。

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("email_agent", "0004_analysisresult_taskcandidate_tasksource"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="emailmessage",
            name="references",
            field=models.TextField(blank=True, verbose_name="邮件引用链"),
        ),
        migrations.AddField(
            model_name="emailmessage",
            name="reply_to",
            field=models.JSONField(default=list, verbose_name="回复地址"),
        ),
        migrations.AddField(
            model_name="mailboxaccount",
            name="can_send",
            field=models.BooleanField(default=False, verbose_name="已授予发送权限"),
        ),
        migrations.CreateModel(
            name="ActionProposal",
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
                        choices=[("create_task", "创建待办"), ("send_reply", "发送回复")],
                        max_length=24,
                        verbose_name="动作",
                    ),
                ),
                (
                    "source_key",
                    models.CharField(max_length=100, unique=True, verbose_name="业务唯一键"),
                ),
                ("version", models.PositiveIntegerField(default=1, verbose_name="内容版本")),
                ("content", models.JSONField(verbose_name="当前已保存内容")),
                ("content_hash", models.CharField(max_length=64, verbose_name="内容摘要")),
                (
                    "source_digest",
                    models.CharField(
                        blank=True, max_length=64, verbose_name="发送前核对的来源摘要"
                    ),
                ),
                (
                    "source_snapshot",
                    models.JSONField(default=dict, verbose_name="本版本审核时的来源快照"),
                ),
                (
                    "review_state",
                    models.CharField(
                        choices=[
                            ("pending", "待审核"),
                            ("approved", "已确认"),
                            ("rejected", "已拒绝"),
                            ("cancelled", "已取消"),
                        ],
                        default="pending",
                        max_length=16,
                        verbose_name="审核状态",
                    ),
                ),
                (
                    "analysis",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="proposals",
                        to="email_agent.analysisresult",
                    ),
                ),
                (
                    "candidate",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="email_agent.taskcandidate",
                    ),
                ),
                (
                    "run",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="proposal",
                        to="email_agent.agentrun",
                    ),
                ),
            ],
            options={
                "verbose_name": "动作审核单",
                "verbose_name_plural": "动作审核单",
                "ordering": ["-updated_at"],
            },
        ),
        migrations.CreateModel(
            name="ApprovalDecision",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("version", models.PositiveIntegerField()),
                (
                    "decision",
                    models.CharField(
                        choices=[
                            ("pending", "待审核"),
                            ("approved", "已确认"),
                            ("rejected", "已拒绝"),
                            ("cancelled", "已取消"),
                        ],
                        max_length=16,
                    ),
                ),
                ("content_hash", models.CharField(max_length=64)),
                ("source_digest", models.CharField(blank=True, max_length=64)),
                ("snapshot", models.JSONField()),
                ("source_snapshot", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL
                    ),
                ),
                (
                    "proposal",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="decisions",
                        to="email_agent.actionproposal",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="email_agent.agentrun"
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ActionExecution",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("version", models.PositiveIntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("executing", "执行中"),
                            ("succeeded", "已完成"),
                            ("accepted", "邮箱服务已受理"),
                            ("failed", "失败，可重试"),
                            ("unknown", "结果待核对，禁止重发"),
                        ],
                        max_length=16,
                    ),
                ),
                ("result", models.JSONField(blank=True, default=dict)),
                ("last_error_code", models.CharField(blank=True, max_length=100)),
                ("error_message", models.CharField(blank=True, max_length=400)),
                (
                    "proposal",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="executions",
                        to="email_agent.actionproposal",
                    ),
                ),
                (
                    "approval",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="email_agent.approvaldecision",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="TaskItem",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("title", models.CharField(max_length=300, verbose_name="标题")),
                ("description", models.TextField(blank=True, verbose_name="说明")),
                (
                    "responsible_name",
                    models.CharField(blank=True, max_length=200, verbose_name="负责人称呼"),
                ),
                (
                    "responsible_email",
                    models.EmailField(blank=True, max_length=320, verbose_name="负责人邮箱"),
                ),
                ("due_date", models.DateField(blank=True, null=True, verbose_name="截止日期")),
                ("due_time", models.TimeField(blank=True, null=True, verbose_name="截止时间")),
                ("timezone_name", models.CharField(max_length=80, verbose_name="日期时区")),
                (
                    "dependency_notes",
                    models.TextField(blank=True, verbose_name="尚未关联的依赖说明"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("todo", "待办"),
                            ("doing", "进行中"),
                            ("blocked", "已阻塞"),
                            ("done", "已完成"),
                            ("cancelled", "已取消"),
                        ],
                        default="todo",
                        max_length=16,
                        verbose_name="状态",
                    ),
                ),
                ("version", models.PositiveIntegerField(default=1, verbose_name="待办版本")),
                (
                    "candidate",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="task",
                        to="email_agent.taskcandidate",
                    ),
                ),
                (
                    "dependencies",
                    models.ManyToManyField(
                        blank=True, related_name="dependents", to="email_agent.taskitem"
                    ),
                ),
                (
                    "execution",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="task",
                        to="email_agent.actionexecution",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL
                    ),
                ),
            ],
            options={
                "verbose_name": "正式待办",
                "verbose_name_plural": "正式待办",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ActionAttempt",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("number", models.PositiveIntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("executing", "执行中"),
                            ("succeeded", "已完成"),
                            ("accepted", "邮箱服务已受理"),
                            ("failed", "失败，可重试"),
                            ("unknown", "结果待核对，禁止重发"),
                        ],
                        max_length=16,
                    ),
                ),
                ("error_code", models.CharField(blank=True, max_length=100)),
                ("summary", models.JSONField(blank=True, default=dict)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "execution",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="attempts",
                        to="email_agent.actionexecution",
                    ),
                ),
            ],
            options={
                "ordering": ["number"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("execution", "number"), name="unique_execution_attempt"
                    )
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="approvaldecision",
            constraint=models.UniqueConstraint(
                fields=("proposal", "version"), name="unique_proposal_version_decision"
            ),
        ),
        migrations.AddConstraint(
            model_name="actionexecution",
            constraint=models.UniqueConstraint(
                fields=("proposal", "version"), name="unique_proposal_version_execution"
            ),
        ),
    ]

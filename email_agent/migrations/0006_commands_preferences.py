# 阶段 5：指令计划、逐封进度、批量动作版本、明确确认的偏好与已读元数据。

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("email_agent", "0005_human_review"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="emailmessage",
            name="is_read",
            field=models.BooleanField(blank=True, null=True, verbose_name="是否已读"),
        ),
        migrations.CreateModel(
            name="CommandPlan",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("submission_key", models.UUIDField(unique=True)),
                ("instruction", models.TextField(verbose_name="用户原始指令")),
                ("timezone_name", models.CharField(max_length=80)),
                ("version", models.PositiveIntegerField(default=1)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("preview", "待核对计划"),
                            ("searched", "已检索"),
                            ("processing", "正在逐批分析"),
                            ("ready", "待审核处理动作"),
                            ("dispatching", "正在执行已确认动作"),
                            ("completed", "已完成"),
                            ("partial", "部分未完成"),
                            ("failed", "处理失败"),
                            ("cancelled", "已取消后续处理"),
                        ],
                        default="preview",
                        max_length=20,
                    ),
                ),
                ("spec", models.JSONField(default=dict, verbose_name="已校验的意图和检索范围")),
                (
                    "preference_snapshot",
                    models.JSONField(default=dict, verbose_name="本计划采用的已确认偏好"),
                ),
                ("provider", models.CharField(max_length=16)),
                ("model_name", models.CharField(max_length=200)),
                ("usage", models.JSONField(default=dict)),
                ("native_query", models.TextField(blank=True)),
                ("scanned", models.PositiveIntegerField(default=0)),
                ("has_more", models.BooleanField(default=False)),
                ("searched_at", models.DateTimeField(blank=True, null=True)),
                ("selection_locked_at", models.DateTimeField(blank=True, null=True)),
                ("approved_manifest", models.JSONField(default=list)),
                ("error_message", models.CharField(blank=True, max_length=500)),
                (
                    "mailbox",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="email_agent.mailboxaccount"
                    ),
                ),
                (
                    "run",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="command",
                        to="email_agent.agentrun",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="CommandItem",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                ("selected", models.BooleanField(default=False)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("found", "待选择处理"),
                            ("done", "已分析"),
                            ("failed", "分析未完成"),
                            ("skipped", "未选择"),
                        ],
                        default="found",
                        max_length=16,
                    ),
                ),
                ("error_message", models.CharField(blank=True, max_length=400)),
                (
                    "analysis",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="email_agent.analysisresult",
                    ),
                ),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="email_agent.emailmessage"
                    ),
                ),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="email_agent.commandplan",
                    ),
                ),
            ],
            options={
                "ordering": ["id"],
            },
        ),
        migrations.CreateModel(
            name="CommandAction",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("proposal_version", models.PositiveIntegerField()),
                ("content_hash", models.CharField(max_length=64)),
                ("dispatched_at", models.DateTimeField(blank=True, null=True)),
                ("error_message", models.CharField(blank=True, max_length=400)),
                (
                    "proposal",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="email_agent.actionproposal"
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="email_agent.commanditem"
                    ),
                ),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="actions",
                        to="email_agent.commandplan",
                    ),
                ),
            ],
            options={
                "ordering": ["id"],
            },
        ),
        migrations.CreateModel(
            name="UserPreference",
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
                    "key",
                    models.CharField(
                        choices=[
                            ("timezone_name", "默认时区"),
                            ("reply_language", "回复语言"),
                            ("reply_tone", "回复风格"),
                            ("signature", "邮件签名"),
                        ],
                        max_length=30,
                        verbose_name="偏好类型",
                    ),
                ),
                ("value", models.CharField(max_length=500, verbose_name="已确认的值")),
                ("enabled", models.BooleanField(default=True, verbose_name="启用")),
                ("version", models.PositiveIntegerField(default=1)),
                ("confirmed_at", models.DateTimeField()),
                ("confirmation_source", models.CharField(max_length=120, verbose_name="确认来源")),
                (
                    "mailbox",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="email_agent.mailboxaccount",
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
                "ordering": ["key", "-updated_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="commanditem",
            constraint=models.UniqueConstraint(
                fields=("plan", "message"), name="unique_command_message"
            ),
        ),
        migrations.AddConstraint(
            model_name="commandaction",
            constraint=models.UniqueConstraint(
                fields=("plan", "proposal"), name="unique_command_proposal"
            ),
        ),
        migrations.AddConstraint(
            model_name="userpreference",
            constraint=models.UniqueConstraint(
                condition=models.Q(("mailbox__isnull", True)),
                fields=("owner", "key"),
                name="unique_global_preference",
            ),
        ),
        migrations.AddConstraint(
            model_name="userpreference",
            constraint=models.UniqueConstraint(
                condition=models.Q(("mailbox__isnull", False)),
                fields=("owner", "mailbox", "key"),
                name="unique_mailbox_preference",
            ),
        ),
    ]

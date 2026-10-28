# 阶段 1：邮箱账户、一次性授权状态及只读邮件缓存。

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("email_agent", "0002_remove_outbox_event"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MailboxAccount",
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
                    "provider",
                    models.CharField(
                        choices=[("gmail", "Gmail"), ("outlook", "Outlook")],
                        max_length=16,
                        verbose_name="平台",
                    ),
                ),
                (
                    "provider_account_id",
                    models.CharField(max_length=320, verbose_name="平台账户身份"),
                ),
                ("email_address", models.EmailField(max_length=320, verbose_name="邮箱地址")),
                (
                    "display_name",
                    models.CharField(blank=True, max_length=255, verbose_name="显示名称"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("connected", "已连接"),
                            ("reconnect_required", "需要重新授权"),
                            ("disconnected", "已断开"),
                        ],
                        default="connected",
                        max_length=24,
                        verbose_name="连接状态",
                    ),
                ),
                (
                    "encrypted_credentials",
                    models.TextField(blank=True, verbose_name="加密授权凭据"),
                ),
                (
                    "connected_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="最近授权时间"),
                ),
                (
                    "last_checked_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="最近访问时间"),
                ),
                (
                    "last_error_code",
                    models.CharField(blank=True, max_length=80, verbose_name="最近错误类型"),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="mailboxes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "邮箱账户",
                "verbose_name_plural": "邮箱账户",
                "ordering": ["email_address"],
            },
        ),
        migrations.CreateModel(
            name="EmailMessage",
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
                    "provider_message_id",
                    models.CharField(max_length=1024, verbose_name="平台邮件 ID"),
                ),
                (
                    "provider_thread_id",
                    models.CharField(
                        blank=True, db_index=True, max_length=1024, verbose_name="平台会话 ID"
                    ),
                ),
                (
                    "internet_message_id",
                    models.CharField(blank=True, max_length=1024, verbose_name="邮件 Message-ID"),
                ),
                ("subject", models.CharField(blank=True, max_length=998, verbose_name="主题")),
                ("sender", models.JSONField(default=dict, verbose_name="发件人")),
                ("to_recipients", models.JSONField(default=list, verbose_name="收件人")),
                ("cc_recipients", models.JSONField(default=list, verbose_name="抄送")),
                (
                    "received_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="收到时间"),
                ),
                ("sent_at", models.DateTimeField(blank=True, null=True, verbose_name="发送时间")),
                ("snippet", models.TextField(blank=True, verbose_name="正文预览")),
                ("body_text", models.TextField(blank=True, verbose_name="纯文本正文")),
                ("body_loaded", models.BooleanField(default=False, verbose_name="正文已读取")),
                ("body_truncated", models.BooleanField(default=False, verbose_name="正文被截断")),
                (
                    "body_fetched_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="正文读取时间"),
                ),
                ("attachments", models.JSONField(default=list, verbose_name="附件元信息")),
                (
                    "mailbox",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="emails",
                        to="email_agent.mailboxaccount",
                    ),
                ),
            ],
            options={
                "verbose_name": "邮件缓存",
                "verbose_name_plural": "邮件缓存",
                "ordering": ["-received_at", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="OAuthAttempt",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "provider",
                    models.CharField(
                        choices=[("gmail", "Gmail"), ("outlook", "Outlook")], max_length=16
                    ),
                ),
                ("state_digest", models.CharField(max_length=64, unique=True)),
                ("session_digest", models.CharField(max_length=64)),
                ("encrypted_flow", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL
                    ),
                ),
                (
                    "target_account",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="email_agent.mailboxaccount",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="mailboxaccount",
            constraint=models.UniqueConstraint(
                fields=("owner", "provider", "provider_account_id"),
                name="unique_owned_mailbox_identity",
            ),
        ),
        migrations.AddIndex(
            model_name="emailmessage",
            index=models.Index(
                fields=["mailbox", "provider_thread_id"], name="email_agent_mailbox_3d525d_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="emailmessage",
            constraint=models.UniqueConstraint(
                fields=("mailbox", "provider_message_id"), name="unique_mailbox_message"
            ),
        ),
    ]

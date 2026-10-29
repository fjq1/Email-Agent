# 新邮件与多轮改稿；先回填旧审核单的发件邮箱，再收紧为非空。

import uuid

import django.db.models.deletion
from django.db import migrations, models


def backfill_mailbox(apps, schema_editor):
    Proposal = apps.get_model("email_agent", "ActionProposal")
    for proposal in (
        Proposal.objects.using(schema_editor.connection.alias)
        .select_related("analysis__message")
        .iterator()
    ):
        proposal.mailbox_id = proposal.analysis.message.mailbox_id
        proposal.preference_snapshot = proposal.analysis.source_snapshot.get(
            "confirmed_preferences", {}
        )
        proposal.save(
            using=schema_editor.connection.alias, update_fields=["mailbox", "preference_snapshot"]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("email_agent", "0006_commands_preferences"),
    ]

    operations = [
        migrations.AddField(
            model_name="actionproposal",
            name="mailbox",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to="email_agent.mailboxaccount",
            ),
        ),
        migrations.AddField(
            model_name="actionproposal",
            name="preference_snapshot",
            field=models.JSONField(default=dict, verbose_name="草稿采用的已确认偏好"),
        ),
        migrations.AddField(
            model_name="commandplan",
            name="draft_result",
            field=models.JSONField(default=dict, verbose_name="已生成的新邮件草稿，供中断后复用"),
        ),
        migrations.AlterField(
            model_name="actionproposal",
            name="analysis",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="proposals",
                to="email_agent.analysisresult",
            ),
        ),
        migrations.AlterField(
            model_name="actionproposal",
            name="kind",
            field=models.CharField(
                choices=[
                    ("create_task", "创建待办"),
                    ("send_reply", "发送回复"),
                    ("send_email", "发送新邮件"),
                ],
                max_length=24,
                verbose_name="动作",
            ),
        ),
        migrations.AlterField(
            model_name="commandaction",
            name="item",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="email_agent.commanditem",
            ),
        ),
        migrations.CreateModel(
            name="DraftTurn",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                ("request_id", models.UUIDField(default=uuid.uuid4, unique=True)),
                ("instruction", models.TextField(verbose_name="用户的写信或修改要求")),
                ("base_version", models.PositiveIntegerField()),
                ("result_version", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("generating", "正在修改"),
                            ("succeeded", "已保存新版本"),
                            ("failed", "修改未完成"),
                        ],
                        default="generating",
                        max_length=16,
                    ),
                ),
                ("before", models.JSONField(default=dict, verbose_name="修改前的草稿")),
                ("after", models.JSONField(default=dict, verbose_name="修改后的草稿")),
                ("summary", models.CharField(blank=True, max_length=600, verbose_name="修改说明")),
                ("provider", models.CharField(blank=True, max_length=16)),
                ("model_name", models.CharField(blank=True, max_length=200)),
                ("usage", models.JSONField(default=dict)),
                ("error_message", models.CharField(blank=True, max_length=400)),
                (
                    "proposal",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="draft_turns",
                        to="email_agent.actionproposal",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "id"],
            },
        ),
        migrations.RunPython(backfill_mailbox, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="actionproposal",
            name="mailbox",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, to="email_agent.mailboxaccount"
            ),
        ),
    ]

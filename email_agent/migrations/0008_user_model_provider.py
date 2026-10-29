# 增加用户模型平台偏好类型；既有任务已有平台和模型记录，无需回填。

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("email_agent", "0007_compose_and_conversational_drafts"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userpreference",
            name="key",
            field=models.CharField(
                choices=[
                    ("timezone_name", "默认时区"),
                    ("reply_language", "回复语言"),
                    ("reply_tone", "回复风格"),
                    ("signature", "邮件签名"),
                    ("model_provider", "模型平台"),
                ],
                max_length=30,
                verbose_name="偏好类型",
            ),
        ),
    ]

# 简化为同步执行后，移除已废弃的队列投递表，保留其他业务数据。

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("email_agent", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(
            name="OutboxEvent",
        ),
    ]

"""初始化由 LangGraph 管理的检查点表。"""

from django.core.management.base import BaseCommand, CommandError

from email_agent.workflows.checkpoints import initialize_checkpoints


class Command(BaseCommand):
    help = "初始化 PostgreSQL 检查点表，可以重复执行。"

    def handle(self, *args, **options):
        try:
            initialize_checkpoints()
        except Exception as exc:
            raise CommandError(
                f"检查点初始化失败：{type(exc).__name__}，请检查数据库连接。"
            ) from None
        self.stdout.write(self.style.SUCCESS("检查点表初始化完成。"))

from django.core.management.base import BaseCommand

from email_agent.services.outbox import dispatch_pending


class Command(BaseCommand):
    help = "手动补投数据库中的待执行事件。"

    def handle(self, *args, **options):
        self.stdout.write(f"本次投递 {dispatch_pending()} 条事件。")

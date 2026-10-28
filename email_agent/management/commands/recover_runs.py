from django.core.management.base import BaseCommand

from email_agent.services.runs import recover_stalled


class Command(BaseCommand):
    help = "重新调度长时间未更新的诊断流程。"

    def handle(self, *args, **options):
        self.stdout.write(f"本次重新调度 {recover_stalled()} 条运行。")

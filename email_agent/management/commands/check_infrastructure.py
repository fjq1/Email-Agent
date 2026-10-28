"""输出基础设施检查结果，不展示连接凭据。"""

from django.core.management.base import BaseCommand, CommandError

from email_agent.intelligence.models import provider_statuses
from email_agent.services.health import infrastructure_status


class Command(BaseCommand):
    help = "检查 PostgreSQL、检查点表和模型密钥配置状态。"

    def handle(self, *args, **options):
        checks = infrastructure_status()
        for name, check in checks.items():
            self.stdout.write(
                f"{name}: {'正常' if check['ok'] else '不可用'} {check.get('error', '')}"
            )
        for provider in provider_statuses():
            self.stdout.write(
                f"{provider['label']}: {'已填写密钥，连接待验证' if provider['configured'] else '未配置密钥（不影响基础设施）'}"
            )
        if not all(check["ok"] for check in checks.values()):
            raise CommandError("部分基础设施不可用，请先处理上方检查项。")

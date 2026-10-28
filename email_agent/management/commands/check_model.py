"""按需发起最小模型请求；此命令会使用所选平台的 API 额度。"""

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from email_agent.intelligence.models import PROVIDER_LABELS, get_chat_model


class Command(BaseCommand):
    help = "验证一个模型平台的真实连接，会产生少量 API 用量。"

    def add_arguments(self, parser):
        parser.add_argument("--provider", choices=list(PROVIDER_LABELS), required=True)

    def handle(self, *args, **options):
        try:
            model = get_chat_model(options["provider"])
            response = model.invoke("请只回复 OK。")
            if not response.content:
                raise ValueError("模型未返回内容")
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from None
        except Exception as exc:
            raise CommandError(
                f"模型连接失败：{type(exc).__name__}，请检查密钥、地址、模型名和账户额度。"
            ) from None
        self.stdout.write(
            self.style.SUCCESS(f"{PROVIDER_LABELS[options['provider']]}：模型请求成功。")
        )

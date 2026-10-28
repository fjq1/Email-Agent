"""授权完成后执行真实的只读接入检查，不输出邮件正文或授权凭据。"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from email_agent.models import MailboxAccount
from email_agent.providers.base import MailError
from email_agent.services.mailboxes import read_message, read_thread, search_messages


class Command(BaseCommand):
    help = "检查已连接邮箱的真实检索、正文和会话读取。"

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--account", required=True, help="控制台中邮箱账户的 UUID")
        parser.add_argument("--query", default="")

    def handle(self, *args, **options):
        try:
            owner = get_user_model().objects.get(username=options["username"])
            account = MailboxAccount.objects.get(pk=options["account"], owner=owner)
            results, cursor = search_messages(
                account.id, owner, query=options["query"], page_size=1
            )
            self.stdout.write(
                f"检索成功，本页 {len(results)} 封，{'存在下一页' if cursor else '没有下一页'}。"
            )
            if results:
                message = read_message(account.id, owner, results[0].id)
                self.stdout.write(f"正文读取成功，共 {len(message.body_text)} 字符。")
                if message.provider_thread_id:
                    thread, _ = read_thread(account.id, owner, message.id, page_size=1)
                    self.stdout.write(f"会话读取成功，本页 {len(thread)} 封。")
        except (
            get_user_model().DoesNotExist,
            MailboxAccount.DoesNotExist,
            ValidationError,
            ValueError,
        ):
            raise CommandError("请检查用户名和所属邮箱账户 UUID。") from None
        except MailError as exc:
            raise CommandError(f"接入检查未通过：{exc}（{exc.code}）") from None

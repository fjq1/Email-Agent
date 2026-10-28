"""可以在两个独立进程中启动和恢复同一诊断流程。"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from email_agent.models import AgentRun
from email_agent.services.runs import create_probe, decide_probe, resume_probe


class Command(BaseCommand):
    help = "同步创建、审核或从 PostgreSQL 检查点继续诊断流程。"

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--run-id", type=str)
        parser.add_argument("--decision", choices=AgentRun.Decision.values)

    def handle(self, *args, **options):
        try:
            owner = get_user_model().objects.get(username=options["username"])
            if options["run_id"]:
                if options["decision"]:
                    run = decide_probe(options["run_id"], owner, options["decision"])
                else:
                    run = resume_probe(options["run_id"], owner)
            else:
                if options["decision"]:
                    raise CommandError("审核时必须提供 --run-id。")
                run = create_probe(owner)
        except (get_user_model().DoesNotExist, AgentRun.DoesNotExist, ValidationError, ValueError):
            raise CommandError("请检查用户名、所属运行 ID 和当前审核状态。") from None
        self.stdout.write(f"run_id={run.id}\nstatus={run.status}")
        if run.status == AgentRun.Status.FAILED:
            raise CommandError(f"诊断失败：{run.last_error_code}，可以使用同一运行 ID 继续处理。")

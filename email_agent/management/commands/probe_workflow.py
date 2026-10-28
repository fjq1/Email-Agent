"""可以在两个独立进程中启动和恢复同一诊断流程。"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from email_agent.models import AgentRun
from email_agent.services.runs import create_probe, decide_probe
from email_agent.workflows.probe import advance_run


class Command(BaseCommand):
    help = "创建或恢复诊断流程；--inline 可在本进程执行图，仍使用持久化检查点。"

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--run-id", type=str)
        parser.add_argument("--decision", choices=AgentRun.Decision.values)
        parser.add_argument("--inline", action="store_true")

    def handle(self, *args, **options):
        try:
            owner = get_user_model().objects.get(username=options["username"])
            if options["run_id"]:
                run = AgentRun.objects.get(pk=options["run_id"], owner=owner)
                if options["decision"]:
                    run = decide_probe(run.id, owner, options["decision"])
            else:
                if options["decision"]:
                    raise CommandError("审核时必须提供 --run-id。")
                run = create_probe(owner)
            if options["inline"]:
                advance_run(run.id)
                run.refresh_from_db()
        except (get_user_model().DoesNotExist, AgentRun.DoesNotExist, ValidationError, ValueError):
            raise CommandError("请检查用户名、所属运行 ID 和当前审核状态。") from None
        self.stdout.write(f"run_id={run.id}\nstatus={run.status}")

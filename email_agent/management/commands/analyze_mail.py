"""Web 与命令行共用邮件分析入口，命令输出不包含邮件原文或草稿。"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from email_agent.models import AgentRun, AnalysisResult, EmailMessage
from email_agent.providers.base import MailError
from email_agent.services.analysis import analyze_message, resume_analysis


class Command(BaseCommand):
    help = "分析一封已检索的邮件，或从保存的进度继续；会调用已配置模型。"

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--message", type=str, help="本地邮件 UUID，可从详情页地址取得")
        group.add_argument("--analysis", type=str, help="继续已有分析的 UUID")
        parser.add_argument("--timezone", default=None, help="例如 Asia/Shanghai")

    def handle(self, *args, **options):
        try:
            owner = get_user_model().objects.get(username=options["username"])
            if options["analysis"]:
                analysis = resume_analysis(options["analysis"], owner)
            else:
                analysis = analyze_message(
                    options["message"], owner, timezone_name=options["timezone"]
                )
        except (
            get_user_model().DoesNotExist,
            EmailMessage.DoesNotExist,
            AnalysisResult.DoesNotExist,
            ValidationError,
            MailError,
        ):
            raise CommandError(
                "用户、邮件或分析不存在，或时区/邮箱配置无效。请在网页查看连接状态。"
            ) from None
        self.stdout.write(f"analysis_id={analysis.id}")
        self.stdout.write(f"status={analysis.run.status}")
        if analysis.run.status == AgentRun.Status.FAILED:
            raise CommandError(analysis.error_message)
        self.stdout.write(
            f"分类：{analysis.category_label}；任务候选：{analysis.task_sources.count()}"
        )
        self.stdout.write(f"结果页面：/analyses/{analysis.id}/")

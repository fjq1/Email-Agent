"""用户默认平台与任务固定平台：切换只影响新任务，不改已有任务的记录。"""

from django.conf import settings
from django.core.exceptions import ValidationError

from email_agent.intelligence.models import provider_statuses
from email_agent.models import DraftTurn, UserPreference


def model_selection_state(owner):
    # 即便偏好被禁用，也要返回它的版本，供下一次显式选择时校验并重新启用。
    item = UserPreference.objects.filter(
        owner=owner, mailbox__isnull=True, key=UserPreference.Key.MODEL_PROVIDER
    ).first()
    provider = item.value if item and item.enabled else settings.MAIL_ANALYSIS_PROVIDER
    return {
        "provider": provider,
        "version": item.version if item else 0,
        "options": provider_statuses(provider),
    }


def selected_provider(owner):
    return model_selection_state(owner)["provider"]


def check_task_model(provider, model_name):
    """环境中改了模型名称时要求新建任务，避免把新模型的输出记成原模型。"""
    config = settings.MODEL_PROVIDERS.get(provider)
    if not config or config["model"] != model_name:
        raise ValidationError("本任务使用的模型配置已变化，请重新发起任务。")


def proposal_model(proposal):
    """回复沿用来源分析；新邮件沿用初次生成记录。失败轮次不能改变绑定。"""
    if proposal.analysis_id:
        return {"provider": proposal.analysis.provider, "model_name": proposal.analysis.model_name}
    initial = (
        proposal.draft_turns.filter(status=DraftTurn.Status.SUCCEEDED)
        .order_by("created_at", "id")
        .first()
    )
    if initial:
        return {"provider": initial.provider, "model_name": initial.model_name}
    return {}

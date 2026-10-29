"""统一汇总指令状态；审核与执行账本是事实来源，计划状态只是可修复的汇总值。"""

from django.db import transaction

from email_agent.models import ActionExecution, ActionProposal, AgentRun, CommandPlan
from email_agent.workflows.locking import RunBusyError, run_lock


def run_status(status, error=""):
    if error or status in {CommandPlan.Status.FAILED, CommandPlan.Status.PARTIAL}:
        return AgentRun.Status.FAILED
    if status in {CommandPlan.Status.PROCESSING, CommandPlan.Status.DISPATCHING}:
        return AgentRun.Status.RUNNING
    if status in {CommandPlan.Status.PREVIEW, CommandPlan.Status.READY}:
        return AgentRun.Status.WAITING_REVIEW
    if status == CommandPlan.Status.CANCELLED:
        return AgentRun.Status.REJECTED
    return AgentRun.Status.SUCCEEDED


def set_status(plan, status, error=""):
    """业务步骤更新时也保存尚未落库的进度字段，沿用原有同步编排入口。"""
    plan.status, plan.error_message = status, error[:500]
    plan.save()
    plan.run.status = run_status(status, error)
    plan.run.last_error_code = (
        "command_incomplete" if plan.run.status == AgentRun.Status.FAILED else ""
    )
    plan.run.save(update_fields=["status", "last_error_code", "updated_at"])


def derived_status(plan):
    """有批量决定时仅汇总被冻结的动作版本；否则跟随单项审核的当前版本。"""
    if plan.spec.get("action") == "unsupported":
        return CommandPlan.Status.CANCELLED  # 旧版遗留的拒绝计划也不再显示为待核对。
    if plan.status == CommandPlan.Status.CANCELLED:
        return plan.status  # 用户明确取消的是指令后续处理，不撤销单项操作。
    actions = list(plan.actions.all())
    if not actions:
        return plan.status  # 尚未起草、仅检索或仅摘要的计划保留原流程状态。
    items = [item for item in plan.items.all() if item.selected]
    if any(item.status == "found" for item in items):
        return CommandPlan.Status.PROCESSING
    problems = any(item.status == "failed" for item in items)
    by_id = {action.pk: action for action in actions}
    targets = plan.approved_manifest or [
        {"action_id": action.pk, "version": action.proposal.version} for action in actions
    ]
    waiting_review, waiting_execution = False, False
    for entry in targets:
        action = by_id.get(entry["action_id"])
        if action is None:
            problems = True
            continue
        proposal, version = action.proposal, entry["version"]
        execution = next((row for row in proposal.executions.all() if row.version == version), None)
        if execution:
            if execution.status in {
                ActionExecution.Status.SUCCEEDED,
                ActionExecution.Status.ACCEPTED,
            }:
                continue
            if execution.status == ActionExecution.Status.EXECUTING:
                waiting_execution = True
            else:
                problems = True  # 失败、UNKNOWN 都不能汇总成完成。
        elif version != proposal.version:
            problems = True  # 冻结版本被修改，不能把新版本视为已获批。
        elif proposal.review_state in {
            ActionProposal.ReviewState.REJECTED,
            ActionProposal.ReviewState.CANCELLED,
        }:
            continue  # 不执行也是一个已完成的处理决定；动作仍显示“未执行”。
        elif proposal.review_state == ActionProposal.ReviewState.APPROVED:
            if proposal.run.status == AgentRun.Status.FAILED:
                problems = True
            else:
                waiting_execution = True
        elif plan.approved_manifest:
            if action.dispatched_at:
                problems = True
            else:
                waiting_execution = True
        else:
            waiting_review = True
    if waiting_execution:
        return CommandPlan.Status.DISPATCHING
    if waiting_review:
        return CommandPlan.Status.READY
    return CommandPlan.Status.PARTIAL if problems else CommandPlan.Status.COMPLETED


def display_status(plan):
    """只调整当前响应中的对象，不在 GET 中写库、调用模型或触发邮箱操作。"""
    plan.status = derived_status(plan)
    if plan.status not in {CommandPlan.Status.FAILED, CommandPlan.Status.PARTIAL}:
        plan.error_message = ""
    plan.run.status = run_status(plan.status, plan.error_message)
    return plan


def reconcile_status(plan):
    """调用方持有计划运行锁；同时使用短事务避免覆盖计划的其他并发字段。"""
    with transaction.atomic():
        current = (
            CommandPlan.objects.select_for_update(of=("self",))
            .select_related("run")
            .prefetch_related("items", "actions__proposal__executions", "actions__proposal__run")
            .get(pk=plan.pk)
        )
        status = derived_status(current)
        error = (
            current.error_message
            if status in {CommandPlan.Status.FAILED, CommandPlan.Status.PARTIAL}
            else ""
        )
        if (status, error, run_status(status, error)) != (
            current.status,
            current.error_message,
            current.run.status,
        ):
            set_status(current, status, error)
    plan.refresh_from_db()
    return plan


def sync_proposal_plans(proposal):
    """单项编辑、审核或重试结束后更新所属计划；绝不调用任何执行入口。"""
    plans = CommandPlan.objects.filter(
        actions__proposal=proposal, run__owner_id=proposal.run.owner_id
    ).distinct()
    for plan in plans:
        try:
            with run_lock(plan.run_id):
                reconcile_status(plan)
        except RunBusyError:
            # 另一请求正在处理计划时不阻塞；编排结束会再次汇总，展示层也会核对事实。
            continue

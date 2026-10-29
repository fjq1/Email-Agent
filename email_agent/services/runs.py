"""同步执行入口：先提交业务记录，再启动或恢复工作流。"""

import uuid

from django.core.exceptions import ValidationError
from django.db import transaction

from email_agent.models import AgentRun
from email_agent.services.audit import record_event
from email_agent.workflows.locking import RunBusyError, RunFailedError
from email_agent.workflows.probe import advance_run


def execute_run(run):
    """Web 和命令行共用此入口，调用结束后即可获取当前处理结果。"""
    try:
        advance_run(run.id)
    except RunBusyError:
        raise ValidationError("该流程正在处理，请稍后刷新。") from None
    except RunFailedError:
        # 执行器已保存失败状态，界面据此提供手动重试。
        pass
    run.refresh_from_db()
    return run


def create_probe(owner):
    # 图节点使用独立连接；必须在事务提交后执行，避免节点看不到新记录。
    with transaction.atomic():
        run = AgentRun.objects.create(owner=owner)
        record_event(run, "run_created", f"created:{run.id}", actor_id=owner.pk)
    return execute_run(run)


def decide_probe(run_id, owner, decision):
    with transaction.atomic():
        run = AgentRun.objects.select_for_update().get(
            pk=run_id, owner=owner, kind="infrastructure_probe"
        )
        if decision not in AgentRun.Decision.values:
            raise ValidationError("审核决定必须是确认或拒绝。")
        if run.review_decision:
            if run.review_decision != decision:
                raise ValidationError("该流程已有审核决定，请新建诊断流程。")
        else:
            if run.status != AgentRun.Status.WAITING_REVIEW:
                raise ValidationError("该流程目前不处于等待审核状态。")
            run.review_decision = decision
            run.status = AgentRun.Status.PENDING
            run.save(update_fields=["review_decision", "status", "updated_at"])
            record_event(
                run, "review_decided", f"review:{run.id}", actor_id=owner.pk, decision=decision
            )
    # 相同审核请求可以再次恢复；执行器会复用已完成的结果。
    return execute_run(run)


def resume_probe(run_id, owner):
    """手动恢复中断或失败的运行；未审核的运行仍保持等待审核。"""
    run = AgentRun.objects.get(pk=run_id, owner=owner, kind="infrastructure_probe")
    if run.status in [
        AgentRun.Status.SUCCEEDED,
        AgentRun.Status.REJECTED,
        AgentRun.Status.WAITING_REVIEW,
    ]:
        return run
    record_event(run, "resume_requested", f"resume:{uuid.uuid4()}", actor_id=owner.pk)
    # 不预先改写运行状态，已有执行进程仍由数据库锁保护。
    return execute_run(run)

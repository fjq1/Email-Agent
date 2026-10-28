"""诊断流程的启动、审核和恢复入口；所有修改都检查所属用户。"""

import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from email_agent.models import AgentRun, OutboxEvent
from email_agent.services.audit import record_event

logger = logging.getLogger(__name__)


def wake_dispatcher():
    """队列离线时保留 Outbox，由定时调度或管理命令继续投递。"""
    from email_agent.tasks import dispatch_outbox

    try:
        dispatch_outbox.apply_async(retry=False)
    except Exception as exc:
        logger.warning("队列暂不可用，事件已保存在数据库：%s", type(exc).__name__)


def schedule_run(run):
    event = OutboxEvent.objects.create(run=run)
    transaction.on_commit(wake_dispatcher)
    return event


@transaction.atomic
def create_probe(owner):
    run = AgentRun.objects.create(owner=owner)
    record_event(run, "run_created", f"created:{run.id}", actor_id=owner.pk)
    schedule_run(run)
    return run


@transaction.atomic
def decide_probe(run_id, owner, decision):
    run = AgentRun.objects.select_for_update().get(pk=run_id, owner=owner)
    if decision not in AgentRun.Decision.values:
        raise ValidationError("审核决定必须是确认或拒绝。")
    if run.review_decision:
        if run.review_decision == decision:
            return run
        raise ValidationError("该流程已有审核决定，请新建诊断流程。")
    if run.status != AgentRun.Status.WAITING_REVIEW:
        raise ValidationError("该流程目前不处于等待审核状态。")
    run.review_decision = decision
    run.status = AgentRun.Status.PENDING
    run.save(update_fields=["review_decision", "status", "updated_at"])
    record_event(run, "review_decided", f"review:{run.id}", actor_id=owner.pk, decision=decision)
    schedule_run(run)
    return run


@transaction.atomic
def retry_probe(run_id, owner):
    run = AgentRun.objects.select_for_update().get(pk=run_id, owner=owner)
    if run.status != AgentRun.Status.FAILED:
        raise ValidationError("只有执行失败的流程可以手动重试。")
    run.status = AgentRun.Status.PENDING
    run.last_error_code = ""
    run.save(update_fields=["status", "last_error_code", "updated_at"])
    record_event(run, "retry_requested", f"retry:{uuid.uuid4()}", actor_id=owner.pk)
    schedule_run(run)
    return run


def recover_stalled(limit=100):
    """恢复中途退出的运行；等待审核的流程不会被自动确认。"""
    recovered = 0
    cutoff = timezone.now() - timedelta(seconds=settings.RUN_STALE_SECONDS)
    with transaction.atomic():
        runs = list(
            AgentRun.objects.select_for_update(skip_locked=True)
            .filter(
                status__in=[AgentRun.Status.PENDING, AgentRun.Status.RUNNING],
                updated_at__lt=cutoff,
            )
            .order_by("updated_at")[:limit]
        )
        for run in runs:
            if not run.outbox_events.filter(published_at__isnull=True).exists():
                schedule_run(run)
            run.updated_at = timezone.now()
            run.save(update_fields=["updated_at"])
            recovered += 1
    return recovered

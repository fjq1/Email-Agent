"""验证事务 Outbox、用户隔离和诊断状态变化。"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from email_agent.models import AgentRun, AuditEvent, OperationRecord, OutboxEvent
from email_agent.services.audit import record_event
from email_agent.services.outbox import dispatch_pending
from email_agent.services.runs import create_probe, decide_probe, recover_stalled

pytestmark = pytest.mark.django_db(transaction=True)


def test_creation_and_outbox_commit_together(owner):
    with pytest.raises(RuntimeError), transaction.atomic():
        create_probe(owner)
        raise RuntimeError("模拟事务回滚")
    assert AgentRun.objects.count() == 0
    assert OutboxEvent.objects.count() == 0
    assert AuditEvent.objects.count() == 0


def test_duplicate_approval_is_idempotent(owner):
    run = create_probe(owner)
    run.status = AgentRun.Status.WAITING_REVIEW
    run.save()
    decide_probe(run.id, owner, "approve")
    decide_probe(run.id, owner, "approve")
    assert run.outbox_events.count() == 2
    assert run.audit_events.filter(event_type="review_decided").count() == 1
    with pytest.raises(ValidationError):
        decide_probe(run.id, owner, "reject")


def test_another_user_cannot_approve(owner, django_user_model):
    run = create_probe(owner)
    other = django_user_model.objects.create_user(username="other")
    with pytest.raises(AgentRun.DoesNotExist):
        decide_probe(run.id, other, "approve")


def test_outbox_survives_queue_failure(owner):
    run = create_probe(owner)
    with patch(
        "email_agent.tasks.advance_probe.apply_async", side_effect=ConnectionError("不可用")
    ):
        assert dispatch_pending() == 0
    event = run.outbox_events.get()
    assert event.published_at is None
    assert event.attempts == 1
    assert event.claim_token is None
    OutboxEvent.objects.filter(pk=event.pk).update(available_at=timezone.now())
    with patch("email_agent.tasks.advance_probe.apply_async") as publish:
        assert dispatch_pending() == 1
        assert dispatch_pending() == 0
        publish.assert_called_once()


def test_expired_outbox_claim_is_recoverable(owner):
    run = create_probe(owner)
    run.outbox_events.update(lease_until=timezone.now() - timedelta(seconds=1))
    with patch("email_agent.tasks.advance_probe.apply_async"):
        assert dispatch_pending() == 1


def test_stalled_run_requeues_without_approving_waiting_run(owner):
    run = create_probe(owner)
    run.outbox_events.update(published_at=timezone.now())
    old = timezone.now() - timedelta(hours=1)
    AgentRun.objects.filter(pk=run.pk).update(updated_at=old, status=AgentRun.Status.RUNNING)
    waiting = AgentRun.objects.create(owner=owner, status=AgentRun.Status.WAITING_REVIEW)
    AgentRun.objects.filter(pk=waiting.pk).update(updated_at=old)
    assert recover_stalled() == 1
    assert run.outbox_events.filter(published_at__isnull=True).count() == 1
    assert not waiting.outbox_events.exists()


def test_operation_idempotency_is_enforced_by_database(owner):
    run = create_probe(owner)
    values = {
        "run": run,
        "idempotency_key": "unique-action",
        "operation_type": "probe",
        "status": "succeeded",
    }
    OperationRecord.objects.create(**values)
    with pytest.raises(IntegrityError), transaction.atomic():
        OperationRecord.objects.create(**values)


def test_audit_redacts_keys_and_known_secret_values(owner, settings):
    run = create_probe(owner)
    event = record_event(
        run,
        "test",
        "redaction-test",
        api_key="private",
        nested={"refresh_token": "private"},
        message=f"凭据 {settings.SECRET_KEY}",
    )
    assert event.metadata["api_key"] == "[已隐藏]"
    assert event.metadata["nested"]["refresh_token"] == "[已隐藏]"
    assert settings.SECRET_KEY not in event.metadata["message"]

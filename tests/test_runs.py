"""验证同步执行入口、用户隔离和幂等业务记录。"""

from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from email_agent.models import AgentRun, OperationRecord
from email_agent.services.audit import record_event
from email_agent.services.runs import create_probe, decide_probe, resume_probe

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checkpoints")]


def test_creation_reaches_review_before_returning(owner):
    run = create_probe(owner)
    assert run.status == AgentRun.Status.WAITING_REVIEW
    assert run.audit_events.filter(event_type="run_created").count() == 1
    assert not run.operations.exists()


def test_initial_failure_is_preserved_and_can_be_resumed(owner):
    with patch("email_agent.workflows.probe.prepare_node", side_effect=TimeoutError("接口超时")):
        run = create_probe(owner)
    assert run.status == AgentRun.Status.FAILED
    assert run.last_error_code == "TimeoutError"
    assert run.audit_events.filter(event_type="run_failed").count() == 1
    resumed = resume_probe(run.id, owner)
    assert resumed.status == AgentRun.Status.WAITING_REVIEW
    assert resumed.last_error_code == ""


def test_duplicate_approval_is_idempotent(owner):
    run = create_probe(owner)
    assert decide_probe(run.id, owner, "approve").status == AgentRun.Status.SUCCEEDED
    assert decide_probe(run.id, owner, "approve").status == AgentRun.Status.SUCCEEDED
    assert run.operations.count() == 1
    assert run.audit_events.filter(event_type="review_decided").count() == 1
    with pytest.raises(ValidationError):
        decide_probe(run.id, owner, "reject")


def test_invalid_decision_does_not_change_state(owner):
    run = create_probe(owner)
    with pytest.raises(ValidationError):
        decide_probe(run.id, owner, "invalid")
    run.refresh_from_db()
    assert run.status == AgentRun.Status.WAITING_REVIEW
    assert run.review_decision == ""
    assert not run.operations.exists()


@pytest.mark.parametrize("action", ["approve", "resume"])
def test_another_user_cannot_process_run(owner, django_user_model, action):
    run = create_probe(owner)
    other = django_user_model.objects.create_user(username="other")
    with pytest.raises(AgentRun.DoesNotExist):
        if action == "approve":
            decide_probe(run.id, other, "approve")
        else:
            resume_probe(run.id, other)


@pytest.mark.parametrize("status", [AgentRun.Status.PENDING, AgentRun.Status.RUNNING])
def test_interrupted_run_returns_to_review_without_implicit_approval(owner, status):
    run = create_probe(owner)
    # 模拟检查点已保存，但请求尚未更新业务状态时进程退出。
    AgentRun.objects.filter(pk=run.id).update(status=status)
    resumed = resume_probe(run.id, owner)
    assert resumed.status == AgentRun.Status.WAITING_REVIEW
    assert resumed.review_decision == ""
    assert not run.operations.exists()


def test_saved_approval_survives_interrupted_request(owner):
    run = create_probe(owner)
    # 模拟审核事务已提交，尚未调用图执行器时进程退出。
    AgentRun.objects.filter(pk=run.id).update(
        status=AgentRun.Status.PENDING, review_decision="approve"
    )
    assert resume_probe(run.id, owner).status == AgentRun.Status.SUCCEEDED
    assert run.operations.count() == 1


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

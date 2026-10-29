"""验证 PostgreSQL 检查点重放、同步重试和数据库执行锁。"""

from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connections

from email_agent.models import AgentRun, OperationRecord
from email_agent.services.runs import create_probe, decide_probe, resume_probe
from email_agent.workflows.locking import run_lock
from email_agent.workflows.probe import advance_run, complete_node

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checkpoints")]


@pytest.mark.parametrize("decision,expected", [("approve", "succeeded"), ("reject", "rejected")])
def test_pause_resume_and_duplicate_execution(owner, decision, expected):
    run = create_probe(owner)
    assert run.status == AgentRun.Status.WAITING_REVIEW
    assert advance_run(run.id) == AgentRun.Status.WAITING_REVIEW
    assert not run.operations.exists()
    assert decide_probe(run.id, owner, decision).status == expected
    assert resume_probe(run.id, owner).status == expected
    assert run.operations.count() == 1


def test_retry_after_result_committed_before_checkpoint(owner):
    run = create_probe(owner)

    def crash_after_commit(state):
        complete_node(state)
        raise RuntimeError("模拟结果已写入但检查点尚未保存时退出")

    with patch("email_agent.workflows.probe.complete_node", side_effect=crash_after_commit):
        failed = decide_probe(run.id, owner, "approve")
    assert failed.status == AgentRun.Status.FAILED
    assert failed.review_decision == "approve"
    assert failed.last_error_code == "RuntimeError"
    assert OperationRecord.objects.filter(run=run).count() == 1
    assert resume_probe(run.id, owner).status == AgentRun.Status.SUCCEEDED
    assert OperationRecord.objects.filter(run=run).count() == 1
    assert run.audit_events.filter(event_type="probe_recorded").count() == 1


def test_active_run_is_not_overwritten_by_another_request(owner):
    run = create_probe(owner)
    AgentRun.objects.filter(pk=run.id).update(status=AgentRun.Status.RUNNING)

    def competing_request():
        try:
            with pytest.raises(ValidationError, match="正在处理"):
                resume_probe(run.id, owner)
        finally:
            connections.close_all()

    with run_lock(run.id), ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(competing_request).result(timeout=10)
    run.refresh_from_db()
    assert run.status == AgentRun.Status.RUNNING
    assert not run.operations.exists()
    assert resume_probe(run.id, owner).status == AgentRun.Status.WAITING_REVIEW


def test_management_command_uses_the_same_synchronous_entry(owner):
    output = StringIO()
    call_command("probe_workflow", username=owner.username, stdout=output)
    run = AgentRun.objects.get(owner=owner)
    assert "status=waiting_review" in output.getvalue()
    output = StringIO()
    call_command(
        "probe_workflow",
        username=owner.username,
        run_id=str(run.id),
        decision="approve",
        stdout=output,
    )
    assert "status=succeeded" in output.getvalue()
    assert run.operations.count() == 1

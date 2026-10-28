"""每次调用都会重建图和检查点连接，验证持久化恢复及节点重放。"""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from django.db import connections

from email_agent.models import AgentRun, OperationRecord
from email_agent.services.runs import create_probe, decide_probe, retry_probe
from email_agent.workflows.probe import RunBusyError, advance_run, complete_node, run_lock

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("decision,expected", [("approve", "succeeded"), ("reject", "rejected")])
def test_pause_resume_and_duplicate_execution(owner, checkpoints, decision, expected):
    run = create_probe(owner)
    assert advance_run(run.id) == "waiting_review"
    assert OperationRecord.objects.filter(run=run).count() == 0
    assert advance_run(run.id) == "waiting_review"
    decide_probe(run.id, owner, decision)
    assert advance_run(run.id) == expected
    assert advance_run(run.id) == expected
    assert OperationRecord.objects.filter(run=run).count() == 1


def test_retry_after_result_committed_before_checkpoint(owner, checkpoints):
    run = create_probe(owner)
    advance_run(run.id)
    decide_probe(run.id, owner, "approve")

    def crash_after_commit(state):
        complete_node(state)
        raise RuntimeError("模拟结果已写入但检查点尚未保存时退出")

    with patch("email_agent.workflows.probe.complete_node", side_effect=crash_after_commit):
        with pytest.raises(RuntimeError):
            advance_run(run.id)
    run.refresh_from_db()
    assert run.status == AgentRun.Status.FAILED
    assert OperationRecord.objects.filter(run=run).count() == 1
    retry_probe(run.id, owner)
    assert advance_run(run.id) == AgentRun.Status.SUCCEEDED
    assert OperationRecord.objects.filter(run=run).count() == 1
    assert run.audit_events.filter(event_type="probe_recorded").count() == 1


def test_database_lock_excludes_another_connection(owner):
    run = create_probe(owner)

    def competing_worker():
        try:
            with pytest.raises(RunBusyError), run_lock(run.id):
                raise AssertionError("另一个数据库连接不应取得同一流程的执行锁")
        finally:
            connections.close_all()

    with run_lock(run.id), ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(competing_worker).result(timeout=10)

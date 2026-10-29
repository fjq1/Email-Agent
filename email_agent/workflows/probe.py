"""无模型费用、无外部写入的诊断图，验证暂停和恢复基础设施。"""

import logging
from typing import TypedDict

from django.db import connection, transaction
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from email_agent.models import AgentRun, OperationRecord
from email_agent.services.audit import record_event
from email_agent.workflows.checkpoints import open_checkpointer
from email_agent.workflows.locking import RunFailedError, run_lock

logger = logging.getLogger(__name__)


class ProbeState(TypedDict, total=False):
    run_id: str
    prepared: bool
    decision: str
    operation_id: str


def prepare_node(state: ProbeState):
    return {"prepared": True}


def review_node(state: ProbeState):
    # 中断节点恢复时从头执行，因此这里不创建任务、不发送邮件。
    decision = interrupt(
        {
            "kind": "infrastructure_probe",
            "run_id": state["run_id"],
            "message": "确认后仅写入一条诊断操作结果。",
        }
    )
    if decision not in AgentRun.Decision.values:
        raise ValueError("诊断审核结果无效。")
    return {"decision": decision}


def complete_node(state: ProbeState):
    with transaction.atomic():
        run = AgentRun.objects.get(pk=state["run_id"])
        operation, _ = OperationRecord.objects.get_or_create(
            idempotency_key=f"probe:{run.id}",
            defaults={
                "run": run,
                "operation_type": "infrastructure_probe",
                "status": OperationRecord.Status.SUCCEEDED,
                "result": {"decision": state["decision"], "external_action": False},
            },
        )
        record_event(run, "probe_recorded", f"completed:{run.id}", operation_id=str(operation.id))
    return {"operation_id": str(operation.id)}


def build_probe_graph(checkpointer):
    builder = StateGraph(ProbeState)
    builder.add_node("prepare", prepare_node)
    builder.add_node("review", review_node)
    builder.add_node("complete", complete_node)
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "review")
    builder.add_edge("review", "complete")
    builder.add_edge("complete", END)
    return builder.compile(checkpointer=checkpointer)


def advance_run(run_id):
    run = AgentRun.objects.get(pk=run_id)
    if run.kind != "infrastructure_probe":
        raise ValueError("该执行器只接受基础设施诊断流程。")
    if connection.in_atomic_block:
        raise ValueError("请在数据库事务提交后执行工作流。")
    with run_lock(run.id):
        run.refresh_from_db()
        if run.status in [AgentRun.Status.SUCCEEDED, AgentRun.Status.REJECTED]:
            return run.status
        run.status = AgentRun.Status.RUNNING
        run.last_error_code = ""
        run.save(update_fields=["status", "last_error_code", "updated_at"])
        try:
            with open_checkpointer() as saver:
                graph = build_probe_graph(saver)
                config = {"configurable": {"thread_id": str(run.thread_id)}}
                snapshot = graph.get_state(config)
                if not snapshot.values:
                    graph.invoke({"run_id": str(run.id)}, config, durability="sync")
                elif snapshot.interrupts and run.review_decision:
                    graph.invoke(Command(resume=run.review_decision), config, durability="sync")
                elif snapshot.next and not snapshot.interrupts:
                    graph.invoke(None, config, durability="sync")
                snapshot = graph.get_state(config)
                if snapshot.interrupts:
                    run.status = AgentRun.Status.WAITING_REVIEW
                else:
                    decision = snapshot.values.get("decision")
                    if decision not in AgentRun.Decision.values or not snapshot.values.get(
                        "operation_id"
                    ):
                        raise ValueError("工作流最终状态不完整，请检查检查点与流程版本。")
                    run.status = (
                        AgentRun.Status.SUCCEEDED
                        if decision == AgentRun.Decision.APPROVE
                        else AgentRun.Status.REJECTED
                    )
                    run.result = {
                        "decision": decision,
                        "operation_id": snapshot.values.get("operation_id"),
                    }
                run.save(update_fields=["status", "result", "updated_at"])
        except Exception as exc:
            run.status = AgentRun.Status.FAILED
            run.last_error_code = type(exc).__name__
            run.save(update_fields=["status", "last_error_code", "updated_at"])
            record_event(
                run,
                "run_failed",
                f"failed:{run.id}:{run.updated_at.isoformat()}",
                error_code=type(exc).__name__,
            )
            logger.warning("诊断处理失败，错误类型：%s", type(exc).__name__)
            raise RunFailedError(type(exc).__name__) from None
    return run.status

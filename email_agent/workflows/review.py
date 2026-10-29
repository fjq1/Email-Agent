"""真实动作的人工中断：审核节点没有外部副作用，批准后才进入执行节点。"""

import logging
from typing import TypedDict

from django.db import connection
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from email_agent.models import ActionExecution, ActionProposal, AgentRun, ApprovalDecision
from email_agent.services.audit import record_event
from email_agent.services.execution import execute_approved
from email_agent.workflows.checkpoints import open_checkpointer
from email_agent.workflows.locking import run_lock

logger = logging.getLogger(__name__)


class ReviewState(TypedDict, total=False):
    proposal_id: str
    run_id: str
    decision_id: int
    approved: bool
    execution_id: str


class ExecutionIncomplete(Exception):
    """已保存失败或待核对结果，让检查点保留在执行节点。"""


def review_node(state: ReviewState):
    proposal = ActionProposal.objects.get(pk=state["proposal_id"], run_id=state["run_id"])
    value = interrupt(
        {
            "proposal_id": str(proposal.id),
            "version": proposal.version,
            "content_hash": proposal.content_hash,
        }
    )
    receipt = ApprovalDecision.objects.get(
        pk=value["decision_id"],
        proposal=proposal,
        run_id=state["run_id"],
        version=proposal.version,
        content_hash=proposal.content_hash,
    )
    return {
        "decision_id": receipt.pk,
        "approved": receipt.decision == ActionProposal.ReviewState.APPROVED,
    }


def execute_node(state: ReviewState):
    proposal = ActionProposal.objects.select_related("run", "analysis__message__mailbox").get(
        pk=state["proposal_id"], run_id=state["run_id"]
    )
    execution = execute_approved(proposal, state["decision_id"])
    if execution.status in {ActionExecution.Status.FAILED, ActionExecution.Status.UNKNOWN}:
        raise ExecutionIncomplete(execution.last_error_code)
    return {"execution_id": str(execution.id)}


def build_review_graph(checkpointer):
    builder = StateGraph(ReviewState)
    builder.add_node("review", review_node)
    builder.add_node("execute", execute_node)
    builder.add_edge(START, "review")
    builder.add_conditional_edges("review", lambda state: "execute" if state["approved"] else END)
    builder.add_edge("execute", END)
    return builder.compile(checkpointer=checkpointer)


def advance_review(proposal_id):
    if connection.in_atomic_block:
        raise ValueError("审核事务提交后才能启动或恢复工作流。")
    proposal = ActionProposal.objects.select_related("run").get(pk=proposal_id)
    run = proposal.run
    with run_lock(run.id):
        proposal.refresh_from_db()
        if proposal.run_id != run.id:
            return
        run.refresh_from_db()
        if run.status in {AgentRun.Status.SUCCEEDED, AgentRun.Status.REJECTED}:
            return
        execution = proposal.executions.filter(version=proposal.version).first()
        if execution and execution.status == ActionExecution.Status.UNKNOWN:
            return
        run.status, run.last_error_code = AgentRun.Status.RUNNING, ""
        run.save(update_fields=["status", "last_error_code", "updated_at"])
        try:
            with open_checkpointer() as saver:
                graph = build_review_graph(saver)
                config = {"configurable": {"thread_id": str(run.thread_id)}}
                snapshot = graph.get_state(config)
                if not snapshot.values:
                    graph.invoke(
                        {"proposal_id": str(proposal.id), "run_id": str(run.id)},
                        config,
                        durability="sync",
                    )
                    snapshot = graph.get_state(config)
                receipt = proposal.decisions.filter(version=proposal.version, run=run).first()
                if snapshot.interrupts and receipt:
                    graph.invoke(
                        Command(resume={"decision_id": receipt.pk}), config, durability="sync"
                    )
                elif snapshot.next and not snapshot.interrupts:
                    graph.invoke(None, config, durability="sync")
                snapshot = graph.get_state(config)
                if snapshot.interrupts:
                    run.status = AgentRun.Status.WAITING_REVIEW
                else:
                    run.status = (
                        AgentRun.Status.SUCCEEDED
                        if snapshot.values.get("approved")
                        else AgentRun.Status.REJECTED
                    )
                    run.result = {
                        "proposal_id": str(proposal.id),
                        "version": proposal.version,
                        "decision_id": snapshot.values.get("decision_id"),
                        "execution_id": snapshot.values.get("execution_id"),
                    }
                run.save(update_fields=["status", "result", "updated_at"])
        except Exception as exc:
            run.status = AgentRun.Status.FAILED
            run.last_error_code = type(exc).__name__
            run.save(update_fields=["status", "last_error_code", "updated_at"])
            record_event(
                run,
                "review_run_failed",
                f"review-failed:{run.id}:{run.updated_at.isoformat()}",
                error_code=type(exc).__name__,
            )
            logger.warning("审核执行未完成，类型：%s", type(exc).__name__)

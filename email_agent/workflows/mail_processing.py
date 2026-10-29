"""邮件理解工作流：理解与落库分步持久化，重试可复用已经保存的模型结果。"""

import logging
from typing import TypedDict

from django.conf import settings
from django.db import connection
from langgraph.graph import END, START, StateGraph

from email_agent.intelligence.understanding import UnderstandingError, understand_mail
from email_agent.models import AgentRun, AnalysisResult
from email_agent.services.analysis import analysis_input_key, store_candidates
from email_agent.services.audit import record_event
from email_agent.workflows.checkpoints import open_checkpointer
from email_agent.workflows.locking import RunFailedError, run_lock

logger = logging.getLogger(__name__)


class AnalysisState(TypedDict, total=False):
    # 原文和模型结果在业务表中有唯一副本，检查点只保存引用及节点进度。
    analysis_id: str
    understood: bool
    candidates_saved: bool


def understand_node(state: AnalysisState):
    analysis = AnalysisResult.objects.get(pk=state["analysis_id"])
    if not analysis.payload:
        # 配置变化后不要把不同模型的结果记在原模型名下。已有结果仍可正常展示。
        config = settings.MODEL_PROVIDERS[analysis.provider]
        if (
            analysis_input_key(
                analysis.message_id, analysis.source_snapshot, analysis.provider, config
            )
            != analysis.input_key
        ):
            raise UnderstandingError(
                "模型配置或提示词版本已变化，请回到邮件页面发起新的分析。", "model_changed"
            )
        payload, usage = understand_mail(analysis.source_snapshot, analysis.provider)
        analysis.payload, analysis.usage = payload, usage
        analysis.save(update_fields=["payload", "usage", "updated_at"])
    return {"understood": True}


def candidates_node(state: AnalysisState):
    analysis = AnalysisResult.objects.select_related("message", "run").get(pk=state["analysis_id"])
    store_candidates(analysis)
    return {"candidates_saved": True}


def build_analysis_graph(checkpointer):
    builder = StateGraph(AnalysisState)
    builder.add_node("understand", understand_node)
    builder.add_node("candidates", candidates_node)
    builder.add_edge(START, "understand")
    builder.add_edge("understand", "candidates")
    builder.add_edge("candidates", END)
    return builder.compile(checkpointer=checkpointer)


def advance_analysis(analysis_id):
    if connection.in_atomic_block:
        raise ValueError("请在事务提交后执行邮件分析。")
    analysis = AnalysisResult.objects.select_related("run").get(pk=analysis_id)
    run = analysis.run
    with run_lock(run.id):
        run.refresh_from_db()
        if run.status == AgentRun.Status.SUCCEEDED:
            return
        run.status, run.last_error_code = AgentRun.Status.RUNNING, ""
        run.save(update_fields=["status", "last_error_code", "updated_at"])
        AnalysisResult.objects.filter(pk=analysis.id).update(error_message="")
        try:
            with open_checkpointer() as saver:
                graph = build_analysis_graph(saver)
                config = {"configurable": {"thread_id": str(run.thread_id)}}
                state = graph.get_state(config)
                if not state.values:
                    graph.invoke({"analysis_id": str(analysis.id)}, config, durability="sync")
                elif state.next:
                    graph.invoke(None, config, durability="sync")
                if not graph.get_state(config).values.get("candidates_saved"):
                    raise ValueError("邮件理解工作流尚未完成。")
            run.status = AgentRun.Status.SUCCEEDED
            run.result = {"analysis_id": str(analysis.id), "external_action": False}
            run.save(update_fields=["status", "result", "updated_at"])
            record_event(
                run,
                "mail_analysis_completed",
                f"analysis-completed:{run.id}",
                analysis_id=str(analysis.id),
            )
        except Exception as exc:
            code = exc.code if isinstance(exc, UnderstandingError) else type(exc).__name__
            message = (
                str(exc)
                if isinstance(exc, UnderstandingError)
                else "分析未完成，已保存进度，可以重试。"
            )
            run.status, run.last_error_code = AgentRun.Status.FAILED, code
            run.save(update_fields=["status", "last_error_code", "updated_at"])
            AnalysisResult.objects.filter(pk=analysis.id).update(error_message=message[:400])
            record_event(
                run,
                "mail_analysis_failed",
                f"analysis-failed:{run.id}:{run.updated_at.isoformat()}",
                error_code=code,
            )
            logger.warning("邮件分析失败，类型：%s", code)
            raise RunFailedError(code) from None

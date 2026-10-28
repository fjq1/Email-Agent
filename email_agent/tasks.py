"""任务仅携带数据库 ID，状态和结果保存在 PostgreSQL。"""

import logging

import psycopg
from celery import shared_task
from django.db import OperationalError

from email_agent.services.outbox import dispatch_pending
from email_agent.services.runs import recover_stalled
from email_agent.workflows.probe import RunBusyError, advance_run

logger = logging.getLogger(__name__)


@shared_task(name="email_agent.dispatch_outbox", ignore_result=True)
def dispatch_outbox():
    return dispatch_pending()


@shared_task(name="email_agent.recover_stalled_runs", ignore_result=True)
def recover_stalled_runs():
    return recover_stalled()


@shared_task(
    bind=True,
    name="email_agent.advance_probe",
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=5,
)
def advance_probe(self, run_id):
    try:
        return advance_run(run_id)
    except (RunBusyError, OperationalError, psycopg.OperationalError) as exc:
        logger.warning("诊断运行将重试：%s", type(exc).__name__)
        raise self.retry(
            exc=RuntimeError(type(exc).__name__), countdown=min(2**self.request.retries, 60)
        ) from None
    except Exception as exc:
        # Celery 日志只保留错误类型，防止原始异常泄露连接凭据。
        raise RuntimeError(f"诊断失败：{type(exc).__name__}；请查看运行记录。") from None

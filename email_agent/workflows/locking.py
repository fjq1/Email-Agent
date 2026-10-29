"""同步工作流共用的执行锁与异常，避免诊断和邮件流程互相依赖。"""

from contextlib import contextmanager

from django.db import connection


class RunBusyError(Exception):
    """同一工作流正被其他进程处理。"""


class RunFailedError(Exception):
    """失败信息已保存，可由用户手动重试。"""


@contextmanager
def run_lock(run_id):
    # 会话级咨询锁在进程退出、数据库连接断开后自动释放。
    lock_id = int.from_bytes(run_id.bytes[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
        acquired = cursor.fetchone()[0]
    if not acquired:
        raise RunBusyError("同一流程正在执行。")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])

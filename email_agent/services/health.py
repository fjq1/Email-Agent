"""检查连接和检查点表，不调用收费模型接口。"""

from django.db import connection

from email_agent.workflows.checkpoints import open_checkpointer


def infrastructure_status():
    checks = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["postgresql"] = {"ok": True}
    except Exception as exc:
        checks["postgresql"] = {"ok": False, "error": type(exc).__name__}
    try:
        with open_checkpointer() as saver:
            with saver.conn.cursor() as cursor:
                cursor.execute("SELECT 1 FROM checkpoints LIMIT 1")
        checks["checkpoints"] = {"ok": True}
    except Exception as exc:
        checks["checkpoints"] = {"ok": False, "error": type(exc).__name__}
    return checks

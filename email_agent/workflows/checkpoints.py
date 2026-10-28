"""检查点默认使用与 Django 相同的 PostgreSQL 数据库。"""

from contextlib import contextmanager

from django.conf import settings
from django.db import connections
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import make_conninfo


def checkpoint_connection_string():
    if settings.LANGGRAPH_DATABASE_URL:
        return settings.LANGGRAPH_DATABASE_URL
    config = connections["default"].settings_dict
    return make_conninfo(
        dbname=config["NAME"],
        user=config["USER"],
        password=config["PASSWORD"],
        host=config["HOST"],
        port=config["PORT"],
        connect_timeout=config["OPTIONS"].get("connect_timeout", 5),
        sslmode=config["OPTIONS"].get("sslmode", "prefer"),
    )


@contextmanager
def open_checkpointer():
    # 每次运行独立管理连接，避免 fork 后复用父进程连接。
    with PostgresSaver.from_conn_string(checkpoint_connection_string()) as saver:
        yield saver


def initialize_checkpoints():
    """仅在部署初始化或测试建库时调用。"""
    with open_checkpointer() as saver:
        saver.setup()

"""测试使用真实 PostgreSQL，禁止悄悄回退到 SQLite。"""

from .base import *  # noqa: F403

DEBUG = False
LANGGRAPH_DATABASE_URL = ""
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
DATABASES["default"]["CONN_MAX_AGE"] = 0  # noqa: F405
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

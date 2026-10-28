"""Celery 只调度工作，运行结果以业务数据库为准。"""

import os
from pathlib import Path

import environ
from celery import Celery

environ.Env.read_env(Path(__file__).resolve().parent.parent / ".env", overwrite=False)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
app = Celery("email_agent")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

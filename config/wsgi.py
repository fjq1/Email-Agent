"""WSGI 服务入口，对外提供 application 对象。"""

import os
from pathlib import Path

import environ
from django.core.wsgi import get_wsgi_application

environ.Env.read_env(Path(__file__).resolve().parent.parent / ".env", overwrite=False)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

application = get_wsgi_application()

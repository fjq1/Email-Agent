"""ASGI 服务入口，对外提供 application 对象。"""

import os
from pathlib import Path

import environ
from django.core.asgi import get_asgi_application

environ.Env.read_env(Path(__file__).resolve().parent.parent / ".env", overwrite=False)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

application = get_asgi_application()

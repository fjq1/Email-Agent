#!/usr/bin/env python
"""Django 项目管理命令入口。"""

import os
import sys
from pathlib import Path

import environ


def main():
    """加载本地配置后执行管理命令。"""
    environ.Env.read_env(Path(__file__).resolve().parent / ".env", overwrite=False)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "无法导入 Django，请先使用 uv sync 安装项目依赖，再通过 uv run 执行。"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

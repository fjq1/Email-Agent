"""通过独立命令进程验证同步暂停与恢复，可选重启项目的服务。"""

import argparse
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def wait_for(check, description, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.5)
    raise RuntimeError(f"等待超时：{description}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restart", action="store_true", help="审核暂停后重启 Web")
    parser.add_argument("--restart-storage", action="store_true", help="同时重启 PostgreSQL")
    options = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    import django
    import environ

    environ.Env.read_env(root / ".env", overwrite=False)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()

    from django.contrib.auth import get_user_model
    from django.db import InterfaceError, OperationalError, connections, transaction

    from email_agent.models import AgentRun, AuditEvent, OperationRecord
    from email_agent.workflows.checkpoints import open_checkpointer

    owner = get_user_model().objects.create_user(
        username=f"_stage0_verify_{uuid.uuid4().hex[:12]}", is_active=False
    )

    def run_command(*arguments):
        result = subprocess.run(
            [
                sys.executable,
                str(root / "manage.py"),
                "probe_workflow",
                "--username",
                owner.username,
                *arguments,
            ],
            cwd=root,
            check=True,
            capture_output=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            timeout=60,
        )
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    started = run_command()
    run = AgentRun.objects.get(pk=started["run_id"], owner=owner)
    assert started["status"] == AgentRun.Status.WAITING_REVIEW
    print("独立进程同步执行并暂停：通过")
    if options.restart_storage:
        subprocess.run(
            ["docker", "compose", "restart", "postgres"],
            cwd=root,
            check=True,
            capture_output=True,
            timeout=90,
        )
        connections.close_all()
        print("PostgreSQL 重启：完成")
    if options.restart or options.restart_storage:
        subprocess.run(
            ["docker", "compose", "restart", "web"],
            cwd=root,
            check=True,
            capture_output=True,
            timeout=90,
        )
        print("Web 重启：完成")

    def paused_run_exists():
        try:
            run.refresh_from_db()
            return run.status == AgentRun.Status.WAITING_REVIEW
        except (InterfaceError, OperationalError):
            connections.close_all()
            return False

    wait_for(paused_run_exists, "持久化的待审核运行")
    approved = run_command("--run-id", str(run.id), "--decision", "approve")
    assert approved["status"] == AgentRun.Status.SUCCEEDED
    assert (
        run_command("--run-id", str(run.id), "--decision", "approve")["status"]
        == AgentRun.Status.SUCCEEDED
    )
    assert OperationRecord.objects.filter(run=run).count() == 1
    print("新进程恢复、重复确认与操作去重：通过")

    def web_ready():
        try:
            with urlopen("http://127.0.0.1:8000/health/ready/", timeout=5) as response:
                return response.status == 200
        except (URLError, TimeoutError):
            return False

    wait_for(web_ready, "Web 就绪探针")
    print("Web 就绪探针：通过")
    with open_checkpointer() as saver:
        saver.delete_thread(str(run.thread_id))
    with transaction.atomic():
        AuditEvent.objects.filter(run=run).delete()
        OperationRecord.objects.filter(run=run).delete()
        run.delete()
        owner.delete()
    print("临时验证数据已清理。")


if __name__ == "__main__":
    main()

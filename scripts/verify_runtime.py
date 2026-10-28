"""通过真实队列验证跨进程恢复，可选重启本项目 Web 和 Worker。"""

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
    parser.add_argument("--restart", action="store_true", help="在审核暂停后重启 Web 和 Worker")
    parser.add_argument(
        "--restart-storage",
        action="store_true",
        help="同时重启 PostgreSQL 和 Redis，验证持久化数据",
    )
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

    from email_agent.models import AgentRun, AuditEvent, OperationRecord, OutboxEvent
    from email_agent.services.runs import create_probe, decide_probe
    from email_agent.workflows.checkpoints import open_checkpointer
    from email_agent.workflows.probe import advance_run

    # 临时用户不可登录；成功后只清理由本脚本创建的数据。
    owner = get_user_model().objects.create_user(
        username=f"_stage0_verify_{uuid.uuid4().hex[:12]}", is_active=False
    )
    run = create_probe(owner)

    def has_status(status):
        try:
            run.refresh_from_db()
            return run.status == status
        except (InterfaceError, OperationalError):
            connections.close_all()
            return False

    wait_for(lambda: has_status(AgentRun.Status.WAITING_REVIEW), "Worker 暂停等待审核")
    print("真实队列执行并暂停：通过")
    if options.restart_storage:
        subprocess.run(
            ["docker", "compose", "restart", "postgres", "redis"],
            cwd=root,
            check=True,
            capture_output=True,
            timeout=90,
        )
        connections.close_all()
        print("PostgreSQL / Redis 重启：完成")
    if options.restart or options.restart_storage:
        subprocess.run(
            ["docker", "compose", "restart", "web", "worker"],
            cwd=root,
            check=True,
            capture_output=True,
            timeout=90,
        )
        print("Web / Worker 重启：完成")
    wait_for(lambda: has_status(AgentRun.Status.WAITING_REVIEW), "持久化的待审核运行")
    decide_probe(run.id, owner, "approve")
    decide_probe(run.id, owner, "approve")
    wait_for(lambda: has_status(AgentRun.Status.SUCCEEDED), "重启后恢复完成")
    assert advance_run(run.id) == AgentRun.Status.SUCCEEDED
    assert OperationRecord.objects.filter(run=run).count() == 1
    print("恢复、重复确认与操作去重：通过")

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
        OutboxEvent.objects.filter(run=run).delete()
        AuditEvent.objects.filter(run=run).delete()
        OperationRecord.objects.filter(run=run).delete()
        run.delete()
        owner.delete()
    print("临时验证数据已清理。")


if __name__ == "__main__":
    main()

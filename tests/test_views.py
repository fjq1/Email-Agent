"""验证请求返回时的同步结果、登录、跨用户隔离和 CSRF 保护。"""

from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from email_agent.models import AgentRun
from email_agent.services.health import infrastructure_status

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checkpoints")]


def test_dashboard_requires_login(client):
    assert client.get(reverse("dashboard")).status_code == 302


def test_request_runs_and_approves_synchronously(client, owner):
    assert client.login(username="owner", password="test-password-only")
    assert client.get(reverse("dashboard")).status_code == 200
    assert client.get(reverse("start_probe")).status_code == 405
    assert client.post(reverse("start_probe")).status_code == 302
    run = AgentRun.objects.get(owner=owner)
    assert run.status == AgentRun.Status.WAITING_REVIEW
    response = client.post(
        reverse("review_probe", args=[run.id]), {"decision": "approve"}, follow=True
    )
    run.refresh_from_db()
    assert run.status == AgentRun.Status.SUCCEEDED
    assert "诊断处理完成" in response.content.decode()
    assert run.operations.count() == 1


def test_failure_is_visible_and_manual_retry_finishes_in_request(client, owner):
    client.force_login(owner)
    client.post(reverse("start_probe"))
    run = AgentRun.objects.get(owner=owner)
    with patch("email_agent.workflows.probe.complete_node", side_effect=TimeoutError("模拟超时")):
        response = client.post(
            reverse("review_probe", args=[run.id]), {"decision": "approve"}, follow=True
        )
    run.refresh_from_db()
    assert run.status == AgentRun.Status.FAILED
    assert "诊断处理失败" in response.content.decode()
    assert client.post(reverse("resume_run", args=[run.id])).status_code == 302
    run.refresh_from_db()
    assert run.status == AgentRun.Status.SUCCEEDED
    assert run.operations.count() == 1


def test_resume_without_approval_stays_paused(client, owner):
    client.force_login(owner)
    client.post(reverse("start_probe"))
    run = AgentRun.objects.get(owner=owner)
    client.post(reverse("resume_run", args=[run.id]))
    run.refresh_from_db()
    assert run.status == AgentRun.Status.WAITING_REVIEW
    assert not run.operations.exists()


def test_csrf_is_required(owner):
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    assert client.post(reverse("start_probe")).status_code == 403


def test_other_users_run_is_hidden(client, owner, django_user_model):
    run = AgentRun.objects.create(owner=owner, status=AgentRun.Status.WAITING_REVIEW)
    other = django_user_model.objects.create_user(username="other")
    client.force_login(other)
    assert str(run.id) not in client.get(reverse("dashboard")).content.decode()
    assert (
        client.post(reverse("review_probe", args=[run.id]), {"decision": "approve"}).status_code
        == 404
    )
    assert client.post(reverse("resume_run", args=[run.id])).status_code == 404


def test_health_only_checks_postgresql_and_checkpoints():
    checks = infrastructure_status()
    assert set(checks) == {"postgresql", "checkpoints"}
    assert all(check["ok"] for check in checks.values())


def test_public_health_exposes_no_connection_details(client):
    assert client.get(reverse("liveness")).json() == {"status": "alive"}
    with patch(
        "email_agent.views.infrastructure_status",
        return_value={"postgresql": {"ok": False, "error": "internal"}},
    ):
        response = client.get(reverse("readiness"))
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}

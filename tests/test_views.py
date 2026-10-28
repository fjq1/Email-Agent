"""验证登录、跨用户隔离、POST 和 CSRF 保护。"""

from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from email_agent.models import AgentRun

pytestmark = pytest.mark.django_db


def test_dashboard_requires_login(client):
    assert client.get(reverse("dashboard")).status_code == 302


def test_login_dashboard_and_post_only(client, owner):
    assert client.login(username="owner", password="test-password-only")
    response = client.get(reverse("dashboard"))
    assert response.status_code == 200
    assert "阶段 0" in response.content.decode()
    assert client.get(reverse("start_probe")).status_code == 405
    assert client.post(reverse("start_probe")).status_code == 302
    assert AgentRun.objects.filter(owner=owner).count() == 1


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


def test_public_health_exposes_no_connection_details(client):
    assert client.get(reverse("liveness")).json() == {"status": "alive"}
    with patch(
        "email_agent.views.infrastructure_status",
        return_value={"postgresql": {"ok": False, "error": "internal"}},
    ):
        response = client.get(reverse("readiness"))
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}

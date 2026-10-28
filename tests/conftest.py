"""测试使用真实 PostgreSQL；消息发送在单元测试中隔离。"""

import pytest

from email_agent.workflows.checkpoints import initialize_checkpoints


@pytest.fixture(autouse=True)
def disable_queue_wakeup(monkeypatch):
    monkeypatch.setattr("email_agent.services.runs.wake_dispatcher", lambda: None)


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_user(username="owner", password="test-password-only")


@pytest.fixture
def checkpoints(transactional_db):
    initialize_checkpoints()

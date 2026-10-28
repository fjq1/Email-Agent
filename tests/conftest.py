"""测试使用真实 PostgreSQL 和持久化检查点。"""

import pytest

from email_agent.workflows.checkpoints import initialize_checkpoints


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_user(username="owner", password="test-password-only")


@pytest.fixture
def checkpoints(transactional_db):
    initialize_checkpoints()

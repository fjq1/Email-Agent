"""阶段 3 少量主流程测试：真实检查点与适配器，邮箱 HTTP 使用模拟响应。"""

import base64
import json
import uuid
from datetime import datetime
from email import policy
from email.parser import BytesParser

import httpx
import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from email_agent.models import (
    ActionExecution,
    ActionProposal,
    AgentRun,
    AnalysisResult,
    EmailMessage,
    TaskCandidate,
    TaskItem,
    TaskSource,
)
from email_agent.providers.gmail import GmailProvider
from email_agent.providers.outlook import OutlookProvider
from email_agent.services.analysis import source_snapshot
from email_agent.services.reviews import (
    decide_proposal,
    prepare_proposal,
    restart_proposal,
    resume_proposal,
    save_proposal,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checkpoints")]
BODY = "请提交季度报告，完成后回复确认。"


@pytest.fixture
def analysis(owner, mailbox):
    message = EmailMessage.objects.create(
        mailbox=mailbox,
        provider_message_id="source-message",
        provider_thread_id="thread-1",
        internet_message_id="<source@example.com>",
        subject="季度报告",
        sender={"name": "Alice", "address": "alice@example.com"},
        to_recipients=[{"name": "", "address": "owner@example.com"}],
        body_loaded=True,
        body_text=BODY,
        sent_at=datetime.fromisoformat("2026-10-28T09:00:00+08:00"),
    )
    message.refresh_from_db()
    analysis = AnalysisResult.objects.create(
        message=message,
        run=AgentRun.objects.create(
            owner=owner, kind="mail_analysis", status=AgentRun.Status.SUCCEEDED
        ),
        input_key=uuid.uuid4().hex,
        provider="bailian",
        model_name="test-model",
        prompt_version="test",
        timezone_name="Asia/Shanghai",
        source_snapshot=source_snapshot(message, "Asia/Shanghai"),
        payload={
            "primary_category": "task_required",
            "requires_reply": True,
            "contains_tasks": True,
            "reply_draft": {"subject": "Re: 季度报告", "body": "收到，我会跟进报告。"},
        },
    )
    item = {
        "title": "提交季度报告",
        "description": "准备并提交报告。",
        "owner": {"raw": "李明", "email": None},
        "deadline": None,
        "dependencies": [],
        "uncertainties": [],
    }
    candidate = TaskCandidate.objects.create(
        mailbox=mailbox, dedupe_key=uuid.uuid4().hex, title=item["title"], details=item
    )
    TaskSource.objects.create(candidate=candidate, analysis=analysis, item_index=0, details=item)
    return analysis


@pytest.fixture
def mail_api(analysis, mail_http, monkeypatch):
    posts, behavior = [], {"status": None, "body": BODY}
    # 不访问 OAuth 令牌服务器，HTTP 读取和真实 MIME/Graph 写入代码仍然执行。
    for provider in (GmailProvider, OutlookProvider):
        monkeypatch.setattr(
            provider, "access_token", lambda self, saved, **kwargs: ("test-access", saved)
        )

    def respond(request):
        if request.method == "POST":
            posts.append(request)
            if behavior["status"] == "timeout":
                raise httpx.ReadTimeout("模拟发送后等待响应超时", request=request)
            status = behavior["status"] or (
                202 if "graph.microsoft.com" in request.url.host else 200
            )
            return httpx.Response(
                status, json={"id": "sent-message", "threadId": "thread-1"} if status == 200 else {}
            )
        if request.url.host == "gmail.googleapis.com":
            return httpx.Response(
                200,
                json={
                    "id": "source-message",
                    "threadId": "thread-1",
                    "payload": {
                        "mimeType": "text/plain",
                        "headers": [
                            {"name": "Subject", "value": "季度报告"},
                            {"name": "From", "value": "Alice <alice@example.com>"},
                            {"name": "To", "value": "owner@example.com"},
                            {"name": "Reply-To", "value": "reply@example.com"},
                            {"name": "Message-ID", "value": "<source@example.com>"},
                            {"name": "Date", "value": "Wed, 28 Oct 2026 09:00:00 +0800"},
                        ],
                        "body": {
                            "data": base64.urlsafe_b64encode(behavior["body"].encode()).decode()
                        },
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "source-message",
                "conversationId": "thread-1",
                "internetMessageId": "<source@example.com>",
                "sentDateTime": "2026-10-28T01:00:00Z",
                "subject": "季度报告",
                "from": {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
                "toRecipients": [{"emailAddress": {"name": "", "address": "owner@example.com"}}],
                "replyTo": [{"emailAddress": {"address": "reply@example.com"}}],
                "body": {"contentType": "Text", "content": behavior["body"]},
            },
        )

    mail_http(respond)
    return posts, behavior


def edit_data(proposal, **changes):
    data = {key: value if value is not None else "" for key, value in proposal.content.items()}
    if proposal.kind == "send_reply":
        data.update(to=", ".join(data["to"]), cc=", ".join(data["cc"]))
    return {**data, "version": proposal.version, **changes}


def test_task_edit_preview_approve_duplicate_confirm_and_status(client, owner, analysis):
    client.force_login(owner)
    candidate = analysis.task_sources.get().candidate
    response = client.post(
        reverse("prepare_review", args=[analysis.id]), {"candidate_id": candidate.id}, follow=True
    )
    assert response.status_code == 200
    proposal = ActionProposal.objects.get()
    assert proposal.run.status == AgentRun.Status.WAITING_REVIEW
    assert not TaskItem.objects.exists()
    response = client.post(
        reverse("action_review_edit", args=[proposal.id]),
        edit_data(proposal, title="提交审核后的季度报告", due_date="2026-11-05", due_time="17:00"),
        follow=True,
    )
    assert "提交审核后的季度报告" in response.content.decode()
    proposal.refresh_from_db()
    assert proposal.version == 2
    stale = client.post(
        reverse("action_review_decide", args=[proposal.id]),
        {"version": 1, "decision": "approve"},
        follow=True,
    )
    assert "版本已过期" in stale.content.decode()
    assert not TaskItem.objects.exists()
    for _ in range(2):
        response = client.post(
            reverse("action_review_decide", args=[proposal.id]),
            {"version": 2, "decision": "approve"},
            follow=True,
        )
        assert response.status_code == 200
    task = TaskItem.objects.get()
    assert task.title == "提交审核后的季度报告" and str(task.due_date) == "2026-11-05"
    assert proposal.decisions.get().snapshot["title"] == task.title
    assert ActionExecution.objects.count() == 1
    assert client.get(reverse("tasks")).status_code == 200
    assert (
        client.post(
            reverse("task_detail", args=[task.id]), {"version": 1, "status": "done"}, follow=True
        ).status_code
        == 200
    )
    task.refresh_from_db()
    assert task.status == "done"


@pytest.mark.parametrize("decision,state", [("dismiss", "cancelled"), ("reject", "rejected")])
def test_dismiss_ends_review_until_explicit_restart(client, owner, analysis, decision, state):
    # 同一主流程也覆盖旧版拒绝记录，不另建重复的兼容测试文件。
    proposal = prepare_proposal(
        analysis.id, owner, candidate_id=analysis.task_sources.get().candidate_id
    )
    client.force_login(owner)
    initial = client.get(reverse("action_review", args=[proposal.id])).content.decode()
    assert 'value="dismiss"' in initial and 'value="reject"' not in initial
    proposal = decide_proposal(proposal.id, owner, proposal.version, decision)
    assert proposal.review_state == state
    assert proposal.decisions.get().decision == state
    old_run_id = proposal.run_id
    page = client.get(reverse("action_review", args=[proposal.id])).content.decode()
    assert "重新处理" in page
    assert reverse("action_review_edit", args=[proposal.id]) not in page
    with pytest.raises(ValidationError, match="重新处理"):
        save_proposal(proposal.id, owner, edit_data(proposal))
    client.post(reverse("action_review_edit", args=[proposal.id]), edit_data(proposal))
    proposal.refresh_from_db()
    assert proposal.version == 1  # 隐藏链接和后端保护同时生效。
    response = client.post(
        reverse("action_review_restart", args=[proposal.id]), {"version": 1}, follow=True
    )
    assert response.status_code == 200
    proposal.refresh_from_db()
    assert proposal.review_state == "pending" and proposal.version == 2
    assert proposal.run_id != old_run_id and proposal.decisions.get(version=1).decision == state
    assert reverse("action_review_edit", args=[proposal.id]) in response.content.decode()
    assert "不执行此建议" in response.content.decode()
    # 重复旧请求不能继续创建新版本，也不能让旧版批准生效。
    client.post(reverse("action_review_restart", args=[proposal.id]), {"version": 1})
    with pytest.raises(ValidationError, match="过期"):
        decide_proposal(proposal.id, owner, 1, "approve")
    proposal.refresh_from_db()
    assert proposal.version == 2
    assert not ActionExecution.objects.exists() and not TaskItem.objects.exists()


@pytest.mark.parametrize("provider", ["gmail", "outlook"])
def test_approved_reply_sends_edited_version_once(client, owner, analysis, mail_api, provider):
    mailbox = analysis.message.mailbox
    mailbox.provider, mailbox.can_send = provider, True
    mailbox.save()
    proposal = prepare_proposal(analysis.id, owner)
    assert proposal.content["to"] == ["reply@example.com"]
    client.force_login(owner)
    assert (
        client.post(
            reverse("action_review_edit", args=[proposal.id]),
            edit_data(
                proposal,
                to="recipient@example.com",
                cc="copy@example.com",
                body="我已经核对需求，会继续跟进。",
            ),
            follow=True,
        ).status_code
        == 200
    )
    proposal.refresh_from_db()
    decide_proposal(proposal.id, owner, proposal.version, "approve")
    decide_proposal(proposal.id, owner, proposal.version, "approve")
    assert len(mail_api[0]) == 1
    execution = proposal.executions.get()
    assert execution.status == ("accepted" if provider == "outlook" else "succeeded")
    request = mail_api[0][0]
    payload = json.loads(request.content)
    if provider == "gmail":
        message = BytesParser(policy=policy.default).parsebytes(
            base64.urlsafe_b64decode(payload["raw"])
        )
        assert message["To"] == "recipient@example.com" and message["Cc"] == "copy@example.com"
        assert message["In-Reply-To"] == "<source@example.com>"
        assert "我已经核对需求" in message.get_content()
        assert payload["threadId"] == "thread-1"
    else:
        assert request.url.path.endswith("/source-message/reply")
        assert (
            payload["message"]["toRecipients"][0]["emailAddress"]["address"]
            == "recipient@example.com"
        )
        assert payload["message"]["body"]["content"] == "我已经核对需求，会继续跟进。"
        assert not execution.result["delivery_confirmed"]
    assert client.get(reverse("action_review", args=[proposal.id])).status_code == 200


def test_unknown_send_outcome_is_never_retried(owner, analysis, mail_api):
    mailbox = analysis.message.mailbox
    mailbox.can_send = True
    mailbox.save()
    mail_api[1]["status"] = "timeout"
    proposal = prepare_proposal(analysis.id, owner)
    decide_proposal(proposal.id, owner, 1, "approve")
    assert proposal.executions.get().status == "unknown"
    with pytest.raises(ValidationError, match="重新处理"):
        restart_proposal(proposal.id, owner, 1)
    resume_proposal(proposal.id, owner)
    decide_proposal(proposal.id, owner, 1, "approve")
    assert len(mail_api[0]) == 1


def test_definite_rejection_can_retry_same_approved_version(owner, analysis, mail_api):
    mailbox = analysis.message.mailbox
    mailbox.can_send = True
    mailbox.save()
    mail_api[1]["status"] = 400
    proposal = prepare_proposal(analysis.id, owner)
    decide_proposal(proposal.id, owner, 1, "approve")
    assert proposal.executions.get().status == "failed"
    mail_api[1]["status"] = 200
    resume_proposal(proposal.id, owner)
    assert proposal.executions.get().status == "succeeded"
    assert len(mail_api[0]) == 2
    assert proposal.decisions.count() == 1


def test_send_permission_and_owner_boundaries(client, owner, analysis, mail_api, django_user_model):
    proposal = prepare_proposal(analysis.id, owner)
    client.force_login(owner)
    response = client.post(
        reverse("action_review_decide", args=[proposal.id]),
        {"version": 1, "decision": "approve"},
        follow=True,
    )
    assert "启用发送权限" in response.content.decode()
    assert not mail_api[0] and not proposal.decisions.exists()
    client.force_login(django_user_model.objects.create_user(username="other-reviewer"))
    assert client.get(reverse("action_review", args=[proposal.id])).status_code == 404
    assert (
        client.post(
            reverse("action_review_decide", args=[proposal.id]),
            {"version": 1, "decision": "approve"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            reverse("action_review_edit", args=[proposal.id]), edit_data(proposal)
        ).status_code
        == 404
    )
    assert (
        client.post(
            reverse("action_review_restart", args=[proposal.id]), {"version": 1}
        ).status_code
        == 404
    )


def test_reply_conversational_revision_uses_latest_saved_draft(
    client, owner, analysis, mail_api, monkeypatch
):
    """回复共用改稿服务；手动编辑与对话交替后，从最新版本继续。"""
    from email_agent.services.reviews import save_proposal

    captured = []

    def generate(**data):
        captured.append(data)
        return {
            "content": {
                **data["current"],
                "body": data["current"]["body"] + "\n" + data["instruction"],
            },
            "summary": "已修改。",
            "usage": {},
            "provider": "bailian",
            "model_name": "test-model",
        }

    monkeypatch.setattr("email_agent.services.drafts.generate_draft", generate)
    proposal = prepare_proposal(analysis.id, owner)
    client.force_login(owner)
    client.post(
        reverse("action_review_revise", args=[proposal.id]),
        {
            "version": 1,
            "instruction": "第一轮要求",
            "request_id": uuid.uuid4(),
        },
        follow=True,
    )
    proposal.refresh_from_db()
    proposal = save_proposal(proposal.id, owner, edit_data(proposal, body="人工确认后的正文"))
    response = client.post(
        reverse("action_review_revise", args=[proposal.id]),
        {
            "version": 3,
            "instruction": "第二轮要求",
            "request_id": uuid.uuid4(),
        },
        follow=True,
    )
    assert response.status_code == 200
    proposal.refresh_from_db()
    assert proposal.version == 4 and proposal.review_state == "pending"
    assert captured[1]["current"]["body"] == "人工确认后的正文"
    assert captured[1]["source"]["body_text"] == BODY
    assert len(captured[1]["history"]) == 1 and proposal.draft_turns.count() == 2
    assert not mail_api[0] and not ActionExecution.objects.exists()

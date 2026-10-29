"""阶段 5 的少量主流程回归：模拟外部 HTTP，保留真实模型客户端、数据库与审核图。"""

import base64
import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime

import httpx
import httpx2
import openai
import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from email_agent.models import (
    ActionExecution,
    ActionProposal,
    AgentRun,
    AnalysisResult,
    CommandPlan,
    DraftTurn,
    TaskItem,
    UserPreference,
)
from email_agent.providers.gmail import GmailProvider
from email_agent.providers.outlook import OutlookProvider
from email_agent.services import commands
from email_agent.services.preferences import (
    change_preference,
    effective_preferences,
    save_preference,
)
from email_agent.services.reviews import save_proposal

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checkpoints")]
BODY = "请你在2026年11月5日前提交报告，收到后请回复确认。"


@pytest.fixture
def model_api(monkeypatch, settings):
    settings.MAIL_ANALYSIS_PROVIDER = "bailian"
    settings.MAIL_ANALYSIS_EXTRA_BODY = {}
    settings.MODEL_PROVIDERS = deepcopy(settings.MODEL_PROVIDERS)
    settings.MODEL_PROVIDERS["bailian"]["api_key"] = "test-command-model-key"
    spec = {
        "action": "extract_tasks",
        "filters": {"folder": "inbox"},
        "limit": 3,
        "options": {},
        "preference_suggestions": [],
        "clarifications": [],
        "explanation": "先核对范围，再处理所选邮件。",
    }
    captured = []
    draft_outputs = []

    def respond(request):
        payload = json.loads(request.content)
        value = json.loads(payload["messages"][1]["content"])
        captured.append(value)
        if "draft_mode" in value:
            result = draft_outputs.pop(0)
        elif "instruction" in value:
            result = spec
        else:
            evidence = {"field": "body_text", "quote": BODY}
            result = {
                "primary_category": "task_required",
                "requires_reply": True,
                "contains_tasks": True,
                "needs_review": True,
                "classification_evidence": [evidence],
                "summary": [{"text": "提交报告并回复。", "evidence": [evidence]}],
                "tasks": [
                    {
                        "title": "提交报告",
                        "description": "提交当前邮件要求的报告。",
                        "owner": None,
                        "deadline": {
                            "raw": "2026年11月5日",
                            "date": "2026-11-05",
                            "time": None,
                            "timezone": "Asia/Shanghai",
                            "inferred": True,
                            "evidence": [evidence],
                        },
                        "evidence": [evidence],
                        "dependencies": [],
                        "uncertainties": [],
                    }
                ],
                "reply_draft": {
                    "subject": "Re: " + value["subject"],
                    "body": "收到，我会跟进报告。",
                    "evidence": [evidence],
                    "uncertainties": [],
                },
                "uncertainties": [],
            }
        return httpx2.Response(
            200,
            json={
                "id": "chat-command-test",
                "object": "chat.completion",
                "created": 1,
                "model": settings.MODEL_PROVIDERS["bailian"]["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(result, ensure_ascii=False),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
            },
        )

    original = openai.OpenAI
    with httpx2.Client(transport=httpx2.MockTransport(respond)) as client:
        monkeypatch.setattr(
            openai, "OpenAI", lambda **options: original(**{**options, "http_client": client})
        )
        yield spec, captured, draft_outputs


@pytest.fixture
def mail_api(mail_http, monkeypatch):
    calls = []
    for provider in (GmailProvider, OutlookProvider):
        monkeypatch.setattr(
            provider, "access_token", lambda self, saved, **options: ("test-access", saved)
        )

    def gmail_message(index):
        return {
            "id": f"m{index}",
            "threadId": f"thread-{index}",
            "labelIds": ["INBOX", "UNREAD"],
            "internalDate": str(int(datetime(2026, 10, 29, 12, tzinfo=UTC).timestamp() * 1000)),
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "Subject", "value": f"项目进度 {index}"},
                    {"name": "From", "value": "Alice <alice@example.com>"},
                    {"name": "To", "value": "owner@example.com"},
                    {"name": "Date", "value": "Thu, 29 Oct 2026 20:00:00 +0800"},
                    {"name": "Message-ID", "value": f"<m{index}@example.com>"},
                ],
                "body": {"data": base64.urlsafe_b64encode(BODY.encode()).decode()},
            },
        }

    def respond(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(
                202 if request.url.host == "graph.microsoft.com" else 200,
                json={}
                if request.url.host == "graph.microsoft.com"
                else {"id": "new-mail", "threadId": "new-thread"},
            )
        if request.url.host == "gmail.googleapis.com":
            if request.url.path.endswith("/messages"):
                return httpx.Response(
                    200, json={"messages": [{"id": f"m{i}"} for i in range(1, 4)]}
                )
            return httpx.Response(200, json=gmail_message(int(request.url.path[-1])))
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": f"m{i}",
                        "conversationId": f"thread-{i}",
                        "subject": f"项目进度 {i}",
                        "from": {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
                        "toRecipients": [{"emailAddress": {"address": "owner@example.com"}}],
                        "receivedDateTime": "2026-10-29T12:00:00Z",
                        "isRead": False,
                    }
                    for i in range(1, 4)
                ]
            },
        )

    mail_http(respond)
    return calls


def ready_plan(owner, mailbox, model_api):
    plan = commands.create_command(
        owner,
        mailbox=mailbox,
        instruction="提取收件箱的报告任务，最多3封。",
        timezone_name="Asia/Shanghai",
        submission_key=uuid.uuid4(),
    )
    commands.search_plan(plan.id, owner, plan.version)
    commands.process_plan(
        plan.id, owner, plan.version, list(plan.items.values_list("id", flat=True))
    )
    plan.refresh_from_db()
    return plan


def test_preference_confirmation_scope_edit_disable_delete(client, owner, mailbox):
    client.force_login(owner)
    assert not UserPreference.objects.exists()
    assert client.get(reverse("preferences")).status_code == 200
    assert not UserPreference.objects.exists()
    response = client.post(
        reverse("preferences"),
        {"key": "timezone_name", "value": "Asia/Shanghai", "mailbox": "", "version": 0},
        follow=True,
    )
    assert response.status_code == 200
    scoped = save_preference(owner, key="timezone_name", value="America/New_York", mailbox=mailbox)
    assert effective_preferences(owner, mailbox)["timezone_name"]["value"] == "America/New_York"
    response = client.post(
        reverse("preference_edit", args=[scoped.id]),
        {"value": "Europe/London", "version": 1},
        follow=True,
    )
    assert response.status_code == 200
    scoped.refresh_from_db()
    assert scoped.value == "Europe/London"
    change_preference(scoped.id, owner, scoped.version, "toggle")
    assert effective_preferences(owner, mailbox)["timezone_name"]["value"] == "Asia/Shanghai"
    scoped.refresh_from_db()
    assert (
        client.post(
            reverse("preference_action", args=[scoped.id]),
            {"version": scoped.version, "operation": "delete"},
        ).status_code
        == 302
    )
    assert not UserPreference.objects.filter(pk=scoped.id).exists()


@pytest.mark.parametrize("provider", ["gmail", "outlook"])
def test_command_preview_search_scope_and_repeated_submit(
    client, owner, mailbox, model_api, mail_api, provider
):
    mailbox.provider = provider
    mailbox.save()
    model_api[0].update(
        action="search",
        limit=2,
        filters={
            "folder": "inbox",
            "sender": "alice@example.com",
            "date_from": "2026-10-29",
            "date_to": "2026-10-29",
            "unread": True,
        },
    )
    nonce = uuid.uuid4()
    client.force_login(owner)
    data = {
        "mailbox": mailbox.id,
        "instruction": "查找今天Alice的未读邮件，最多2封。",
        "timezone_name": "Asia/Shanghai",
        "submission_key": nonce,
    }
    assert client.post(reverse("commands"), data, follow=True).status_code == 200
    plan = CommandPlan.objects.get()
    assert not mail_api and not UserPreference.objects.exists()
    assert client.post(reverse("commands"), data).status_code == 302
    assert len(model_api[1]) == 1 and CommandPlan.objects.count() == 1
    response = client.post(
        reverse("command_act", args=[plan.id, "search"]), {"version": 1}, follow=True
    )
    assert response.status_code == 200
    assert plan.items.count() == 2
    assert not ActionProposal.objects.exists()
    request = mail_api[0]
    if provider == "gmail":
        assert 'in:inbox from:"alice@example.com"' in request.url.params["q"]
        assert "after:" in request.url.params["q"]
    else:
        assert request.url.path == "/v1.0/me/mailFolders/inbox/messages"
        assert "received>=" in request.url.params["$search"]


def test_chunked_analysis_and_explicit_batch_approval(client, owner, mailbox, model_api, mail_api):
    save_preference(owner, key="reply_tone", value="concise")
    save_preference(owner, key="signature", value="测试签名")
    plan = ready_plan(owner, mailbox, model_api)
    assert plan.status == "processing" and plan.items.filter(status="done").count() == 2
    assert not TaskItem.objects.exists() and not ActionExecution.objects.exists()
    plan = commands.process_plan(plan.id, owner, plan.version)
    assert plan.status == "ready" and plan.actions.count() == 3
    analysis = plan.items.first().analysis
    assert analysis.payload["reply_draft"]["body"].endswith("测试签名")
    assert model_api[1][1]["handling_style"]["reply_tone"] == "concise"
    assert "signature" not in str(model_api[1][1].get("handling_style"))
    client.force_login(owner)
    assert client.get(reverse("command_detail", args=[plan.id])).status_code == 200
    commands.confirm_batch(
        plan.id, owner, plan.version, list(plan.actions.values_list("id", flat=True))
    )
    plan.refresh_from_db()
    assert plan.status == "dispatching" and TaskItem.objects.count() == 2
    commands.confirm_batch(plan.id, owner, plan.version)
    commands.confirm_batch(plan.id, owner, plan.version)
    assert TaskItem.objects.count() == 3 and ActionExecution.objects.count() == 3
    assert client.get(reverse("command_detail", args=[plan.id])).status_code == 200


def test_edited_proposal_requires_refresh_before_batch_approval(
    owner, mailbox, model_api, mail_api
):
    model_api[0]["limit"] = 2
    plan = ready_plan(owner, mailbox, model_api)
    action = plan.actions.select_related("proposal").first()
    proposal = action.proposal
    data = {key: value if value is not None else "" for key, value in proposal.content.items()}
    save_proposal(
        proposal.id, owner, {**data, "version": proposal.version, "title": "人工修改的报告任务"}
    )
    ids = list(plan.actions.values_list("id", flat=True))
    with pytest.raises(ValidationError, match="更新"):
        commands.confirm_batch(plan.id, owner, plan.version, ids)
    assert not TaskItem.objects.exists()
    plan = commands.refresh_actions(plan.id, owner, plan.version)
    commands.confirm_batch(plan.id, owner, plan.version, ids)
    assert TaskItem.objects.filter(title="人工修改的报告任务").exists()


def test_command_preference_requires_its_own_confirmation_and_owner(
    client, owner, mailbox, model_api, django_user_model
):
    instruction = "记住以后默认使用简洁回复。"
    model_api[0].update(
        action="preference_only",
        preference_suggestions=[{"key": "reply_tone", "value": "concise", "evidence": instruction}],
    )
    plan = commands.create_command(
        owner, mailbox=mailbox, instruction=instruction, submission_key=uuid.uuid4()
    )
    assert not UserPreference.objects.exists()
    client.force_login(owner)
    assert (
        client.post(
            reverse("command_act", args=[plan.id, "preference"]),
            {"version": 1, "index": 0, "expected_version": 0},
            follow=True,
        ).status_code
        == 200
    )
    preference = UserPreference.objects.get()
    assert preference.value == "concise" and preference.mailbox_id == mailbox.id
    client.force_login(django_user_model.objects.create_user(username="another-command-owner"))
    assert client.get(reverse("command_detail", args=[plan.id])).status_code == 404
    assert (
        client.post(reverse("command_act", args=[plan.id, "approve"]), {"version": 1}).status_code
        == 404
    )
    assert client.get(reverse("preference_edit", args=[preference.id])).status_code == 404


@pytest.mark.parametrize("provider", ["gmail", "outlook"])
def test_compose_command_conversation_then_explicit_send(
    client, owner, mailbox, model_api, mail_api, provider, django_user_model
):
    """双平台写信主流程：两轮修改、错误不覆盖、最后明确确认才发送。"""
    from email import policy
    from email.parser import BytesParser

    from email_agent.services.reviews import decide_proposal

    mailbox.provider, mailbox.can_send = provider, True
    mailbox.save()
    save_preference(owner, key="signature", value="测试签名")
    model_api[0].update(
        action="compose_email",
        compose={"to": ["recipient@example.com"], "cc": [], "subject": "", "brief": "邀请讨论项目"},
    )
    model_api[2].extend(
        [
            {
                "subject": "项目讨论邀请",
                "body": "你好，希望与你讨论项目。",
                "summary": "已起草项目邀请。",
            },
            {
                "subject": "项目讨论邀请",
                "body": "你好，方便时一起讨论项目吗？",
                "summary": "语气更友好。",
            },
            {
                "subject": "下周项目讨论",
                "body": "你好，方便时一起讨论项目吗？时间可安排在下周。",
                "cc": ["copy@example.com"],
                "summary": "补充时间并增加抄送。",
            },
            {
                "subject": "不应覆盖",
                "body": "不应覆盖",
                "to": ["invented@example.com"],
                "summary": "错误的收件人。",
            },
        ]
    )
    client.force_login(owner)
    data = {
        "mailbox": mailbox.id,
        "instruction": "向 recipient@example.com 发一封邮件，邀请讨论项目。",
        "submission_key": uuid.uuid4(),
    }
    assert client.post(reverse("commands"), data, follow=True).status_code == 200
    plan = CommandPlan.objects.get()
    proposal = plan.actions.get().proposal
    assert proposal.kind == "send_email" and proposal.analysis_id is None
    assert proposal.mailbox_id == mailbox.id and plan.status == "ready"
    assert not AnalysisResult.objects.exists() and not mail_api
    assert client.post(reverse("commands"), data).status_code == 302
    assert len(model_api[1]) == 2
    page = client.get(reverse("action_review", args=[proposal.id]))
    assert "新邮件审核" in page.content.decode() and "让助理修改草稿" in page.content.decode()
    first = {"version": 1, "instruction": "再友好一些。", "request_id": uuid.uuid4()}
    assert (
        client.post(
            reverse("action_review_revise", args=[proposal.id]), first, follow=True
        ).status_code
        == 200
    )
    client.post(reverse("action_review_revise", args=[proposal.id]), first)
    assert len(model_api[1]) == 3
    client.post(
        reverse("action_review_revise", args=[proposal.id]),
        {
            "version": 2,
            "instruction": "补充可安排在下周，主题改为下周项目讨论，抄送 copy@example.com。",
            "request_id": uuid.uuid4(),
        },
        follow=True,
    )
    proposal.refresh_from_db()
    assert proposal.version == 3 and proposal.review_state == "pending"
    assert "方便时" in model_api[1][3]["current_draft"]["body"]
    assert model_api[1][3]["conversation"][-1]["instruction"] == "再友好一些。"
    assert "方便时" in model_api[1][3]["conversation"][-1]["draft"]["body"]
    assert proposal.content["body"].count("测试签名") == 1
    client.post(
        reverse("action_review_revise", args=[proposal.id]),
        {
            "version": 3,
            "instruction": "改一下语气。",
            "request_id": uuid.uuid4(),
        },
        follow=True,
    )
    proposal.refresh_from_db()
    assert proposal.version == 3 and proposal.content["subject"] == "下周项目讨论"
    assert DraftTurn.objects.filter(status="failed").count() == 1
    assert not mail_api and not ActionExecution.objects.exists()
    with pytest.raises(ValidationError, match="过期"):
        decide_proposal(proposal.id, owner, 1, "approve")
    for _ in range(2):
        client.post(
            reverse("action_review_decide", args=[proposal.id]),
            {"version": 3, "decision": "approve"},
        )
    # 从审核记录发送时，也必须同步指令计划；该计划的旧预览仍是第 1 版。
    plan.refresh_from_db()
    assert plan.status == "completed" and plan.run.status == AgentRun.Status.SUCCEEDED
    detail = client.get(reverse("command_detail", args=[plan.id]))
    assert detail.context["plan"].status == "completed"
    assert detail.context["actions"][0].execution.version == 3
    assert not detail.context["actions"][0].stale
    assert client.get(reverse("commands")).context["plans"][0].status == "completed"

    # 模拟旧版本留下的过期汇总。GET 按事实展示，但不写库或重发；修复服务可补齐存储。
    from email_agent.services.command_status import reconcile_status
    from email_agent.workflows.locking import run_lock

    CommandPlan.objects.filter(pk=plan.id).update(status="ready")
    AgentRun.objects.filter(pk=plan.run_id).update(status=AgentRun.Status.WAITING_REVIEW)
    assert client.get(reverse("commands")).context["plans"][0].status == "completed"
    assert (
        client.get(reverse("command_detail", args=[plan.id])).context["plan"].status == "completed"
    )
    plan.refresh_from_db()
    assert plan.status == "ready"
    with run_lock(plan.run_id):
        reconcile_status(plan)
    assert plan.status == "completed" and plan.run.status == AgentRun.Status.SUCCEEDED
    assert len(mail_api) == 1 and mail_api[0].method == "POST"
    payload = json.loads(mail_api[0].content)
    if provider == "gmail":
        mime = BytesParser(policy=policy.default).parsebytes(
            base64.urlsafe_b64decode(payload["raw"])
        )
        assert "In-Reply-To" not in mime and "References" not in mime and "threadId" not in payload
        assert mime["To"] == "recipient@example.com" and mime["Cc"] == "copy@example.com"
        assert "时间可安排在下周" in mime.get_content()
    else:
        assert mail_api[0].url.path == "/v1.0/me/sendMail"
        assert payload["message"]["body"]["content"] == proposal.content["body"]
        assert (
            payload["message"]["toRecipients"][0]["emailAddress"]["address"]
            == "recipient@example.com"
        )
    client.force_login(django_user_model.objects.create_user(username="other-drafter"))
    assert (
        client.post(reverse("action_review_revise", args=[proposal.id]), first).status_code == 404
    )


def test_individual_unknown_result_keeps_plan_incomplete(
    client, owner, mailbox, model_api, mail_api, monkeypatch
):
    """汇总不能把 UNKNOWN 误报为完成，也不能覆盖用户明确取消指令的决定。"""
    from email_agent.providers.base import SendOutcomeUnknown
    from email_agent.services.command_status import sync_proposal_plans

    mailbox.can_send = True
    mailbox.save()
    model_api[0].update(
        action="compose_email",
        compose={
            "to": ["recipient@example.com"],
            "cc": [],
            "subject": "",
            "brief": "讨论项目",
        },
    )
    model_api[2].append(
        {"subject": "项目讨论", "body": "希望与你讨论项目。", "summary": "已起草。"}
    )
    calls = []

    def uncertain_send(self, *args):
        calls.append(True)
        raise SendOutcomeUnknown()

    monkeypatch.setattr(GmailProvider, "send_mail", uncertain_send)
    plan = commands.create_command(
        owner,
        mailbox=mailbox,
        instruction="向 recipient@example.com 写信讨论项目。",
        submission_key=uuid.uuid4(),
    )
    proposal = plan.actions.get().proposal
    client.force_login(owner)
    client.post(
        reverse("action_review_decide", args=[proposal.id]), {"version": 1, "decision": "approve"}
    )
    plan.refresh_from_db()
    assert plan.status == "partial" and plan.run.status == AgentRun.Status.FAILED
    assert proposal.executions.get().status == "unknown"
    assert client.get(reverse("commands")).context["plans"][0].status == "partial"
    commands.cancel_plan(plan.id, owner, plan.version)
    sync_proposal_plans(proposal)
    plan.refresh_from_db()
    assert plan.status == "cancelled" and len(calls) == 1

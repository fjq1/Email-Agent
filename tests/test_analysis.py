"""阶段 2 的少量主流程回归：真实模型协议、结果落库、去重与失败继续。"""

import json
from copy import deepcopy
from datetime import datetime
from unittest.mock import patch

import httpx2
import openai
import pytest
from django.urls import reverse

from email_agent.intelligence.schemas import MailUnderstanding
from email_agent.intelligence.understanding import ground_result
from email_agent.models import AgentRun, EmailMessage, TaskCandidate, TaskSource
from email_agent.services.analysis import analyze_message, resume_analysis, source_snapshot
from email_agent.workflows.mail_processing import candidates_node

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("checkpoints")]

BODY = "请张三在2026年11月5日前提交季度报告，需先收到财务数据。完成后请回复确认。"


def sample_result():
    evidence = {"field": "body_text", "quote": BODY}
    return {
        "primary_category": "task_required",
        "requires_reply": True,
        "contains_tasks": True,
        "needs_review": True,
        "classification_evidence": [evidence],
        "summary": [{"text": "张三需在11月5日前提交季度报告并回复。", "evidence": [evidence]}],
        "tasks": [
            {
                "title": "提交季度报告",
                "description": "提交季度报告，先取得财务数据。",
                "evidence": [evidence],
                "owner": {
                    "raw": "张三",
                    "email": None,
                    "evidence": [{"field": "body_text", "quote": "张三"}],
                },
                "deadline": {
                    "raw": "2026年11月5日",
                    "date": "2026-11-05",
                    "time": None,
                    "timezone": "Asia/Shanghai",
                    "inferred": True,
                    "evidence": [{"field": "body_text", "quote": "2026年11月5日前"}],
                },
                "dependencies": [
                    {
                        "text": "先收到财务数据",
                        "evidence": [{"field": "body_text", "quote": "需先收到财务数据"}],
                    }
                ],
                "recurring": False,
                "occurrence": None,
                "uncertainties": [],
            }
        ],
        "reply_draft": {
            "subject": "Re: 季度报告",
            "body": "收到，我会跟进报告所需资料，具体进度待确认。",
            "evidence": [{"field": "body_text", "quote": "完成后请回复确认"}],
            "uncertainties": [],
        },
        "uncertainties": [],
    }


@pytest.fixture
def email(mailbox):
    return EmailMessage.objects.create(
        mailbox=mailbox,
        provider_message_id="message-1",
        provider_thread_id="thread-1",
        subject="季度报告",
        sender={"name": "主管", "address": "manager@example.com"},
        body_text=BODY,
        body_loaded=True,
        sent_at=datetime.fromisoformat("2026-10-28T09:00:00+08:00"),
    )


@pytest.fixture
def model_api(monkeypatch, settings):
    """使用真实 LangChain 模型客户端和 Pydantic，只把 HTTP 换成本地模拟响应。"""
    settings.MAIL_ANALYSIS_PROVIDER = "bailian"
    settings.MAIL_ANALYSIS_EXTRA_BODY = {}
    settings.MODEL_PROVIDERS = deepcopy(settings.MODEL_PROVIDERS)
    settings.MODEL_PROVIDERS["bailian"]["api_key"] = "test-analysis-key"
    calls = []
    outputs = [sample_result()]

    def respond(request):
        calls.append(json.loads(request.content))
        result = outputs.pop(0) if len(outputs) > 1 else outputs[0]
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-analysis",
                "object": "chat.completion",
                "created": 1,
                "model": calls[-1]["model"],
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
        yield calls, outputs


@pytest.mark.parametrize("model_provider", ["bailian", "deepseek"])
def test_normal_analysis_page_and_duplicate_click_reuse_result(
    client, owner, email, model_api, settings, model_provider
):
    from email_agent.services.preferences import save_preference

    settings.MODEL_PROVIDERS[model_provider]["api_key"] = "test-analysis-key"
    save_preference(owner, key="model_provider", value=model_provider)
    client.force_login(owner)
    response = client.post(
        reverse("mailbox_analyze", args=[email.mailbox_id, email.id]),
        {"timezone_name": "Asia/Shanghai"},
        follow=True,
    )
    assert response.status_code == 200
    assert "回复草稿" in response.content.decode()
    analysis = email.analyses.get()
    assert analysis.run.status == AgentRun.Status.SUCCEEDED
    assert analysis.provider == model_provider
    assert analysis.model_name == settings.MODEL_PROVIDERS[model_provider]["model"]
    assert analysis.payload["contains_tasks"] and analysis.payload["requires_reply"]
    assert analysis.payload["tasks"][0]["deadline"]["date"] == "2026-11-05"
    assert analysis.payload["summary"][0]["evidence"][0]["start"] == 0
    assert analysis.usage["total_tokens"] == 200
    assert TaskCandidate.objects.count() == 1
    assert analyze_message(email.id, owner, timezone_name="Asia/Shanghai").id == analysis.id
    assert len(model_api[0]) == 1
    assert model_api[0][0]["response_format"] == {"type": "json_object"}
    assert "test-analysis-key" not in str(analysis.source_snapshot)
    candidate = TaskCandidate.objects.get()
    assert client.get(reverse("task_candidates")).status_code == 200
    assert client.get(reverse("task_candidate", args=[candidate.id])).status_code == 200


def test_repeat_reminder_reuses_candidate_but_preserves_changed_deadline(owner, email, model_api):
    first = analyze_message(email.id, owner, timezone_name="Asia/Shanghai")
    reminder = EmailMessage.objects.create(
        mailbox=email.mailbox,
        provider_message_id="reminder",
        provider_thread_id=email.provider_thread_id,
        subject="再次提醒",
        body_loaded=True,
        body_text=BODY.replace("11月5日", "11月6日"),
    )
    model_api[1][0] = json.loads(
        json.dumps(sample_result(), ensure_ascii=False)
        .replace("11月5日", "11月6日")
        .replace("2026-11-05", "2026-11-06")
    )
    second = analyze_message(reminder.id, owner, timezone_name="Asia/Shanghai")
    source = second.task_sources.get()
    assert TaskCandidate.objects.count() == 1
    assert source.merged and source.conflict
    assert source.candidate.details["deadline"]["date"] == "2026-11-05"
    assert source.details["deadline"]["date"] == "2026-11-06"
    assert first.task_sources.get().candidate_id == source.candidate_id


def test_retry_after_model_result_saved_does_not_call_model_again(owner, email, model_api):
    def fail_after_save(state):
        candidates_node(state)
        raise RuntimeError("模拟保存结果后请求中断")

    with patch(
        "email_agent.workflows.mail_processing.candidates_node", side_effect=fail_after_save
    ):
        analysis = analyze_message(email.id, owner, timezone_name="Asia/Shanghai")
    assert analysis.run.status == AgentRun.Status.FAILED
    assert analysis.payload and len(model_api[0]) == 1
    resumed = resume_analysis(analysis.id, owner)
    assert resumed.run.status == AgentRun.Status.SUCCEEDED
    assert len(model_api[0]) == 1
    assert TaskSource.objects.count() == TaskCandidate.objects.count() == 1


def test_bad_evidence_is_corrected_once_without_saving_invented_fact(owner, email, model_api):
    invalid = sample_result()
    invalid["summary"][0]["evidence"][0] = {"field": "body_text", "quote": "邮件中不存在的话"}
    model_api[1][:] = [invalid, sample_result()]
    analysis = analyze_message(email.id, owner, timezone_name="Asia/Shanghai")
    assert analysis.run.status == AgentRun.Status.SUCCEEDED
    assert len(model_api[0]) == 2
    assert "邮件中不存在的话" not in str(analysis.payload)


def test_recurring_instances_are_kept_separate(owner, email, model_api):
    model_api[1][0]["tasks"][0].update(recurring=True, occurrence="2026-10")
    analyze_message(email.id, owner, timezone_name="Asia/Shanghai")
    model_api[1][0]["tasks"][0]["occurrence"] = "2026-11"
    next_email = EmailMessage.objects.create(
        mailbox=email.mailbox,
        provider_message_id="next-month",
        provider_thread_id=email.provider_thread_id,
        subject="下月报告",
        body_loaded=True,
        body_text=BODY,
    )
    analyze_message(next_email.id, owner, timezone_name="Asia/Shanghai")
    assert TaskCandidate.objects.count() == 2
    assert next_email.analyses.get().task_sources.get().possible_duplicates


def test_ambiguous_and_truncated_input_is_marked_for_review(email, settings):
    settings.MAIL_ANALYSIS_MAX_CHARS = len(BODY)
    email.body_text += "后续内容"
    result = sample_result()
    result["tasks"][0]["owner"] = None
    result["tasks"][0]["deadline"]["date"] = None
    payload = ground_result(
        MailUnderstanding.model_validate(result), source_snapshot(email, "Asia/Shanghai")
    )
    assert payload["needs_review"]
    assert "分析输入正文已截断" in str(payload["uncertainties"])
    assert "负责人未明确" in str(payload["tasks"][0]["uncertainties"])


@pytest.mark.parametrize("needs_reply", [False, True])
def test_notification_and_reply_only_regression(owner, email, model_api, needs_reply):
    """两条小样本补齐分类评估集，不额外引入评估框架。"""
    email.body_text = (
        "请回复确认是否参加本周讨论。"
        if needs_reply
        else "系统升级已完成，本邮件仅供知悉，无需回复。"
    )
    email.save(update_fields=["body_text"])
    evidence = {"field": "body_text", "quote": email.body_text}
    result = sample_result()
    result.update(
        primary_category="reply_required" if needs_reply else "notification",
        requires_reply=needs_reply,
        contains_tasks=False,
        needs_review=needs_reply,
        classification_evidence=[evidence],
        summary=[{"text": email.body_text, "evidence": [evidence]}],
        tasks=[],
    )
    if needs_reply:
        result["reply_draft"]["evidence"] = [evidence]
    else:
        result["reply_draft"] = None
    model_api[1][0] = result
    analysis = analyze_message(email.id, owner, timezone_name="Asia/Shanghai")
    assert analysis.payload["primary_category"] == result["primary_category"]
    assert not analysis.task_sources.exists()
    assert bool(analysis.payload["reply_draft"]) == needs_reply


def test_other_user_cannot_read_analyze_or_retry(
    client, owner, email, django_user_model, model_api
):
    analysis = analyze_message(email.id, owner, timezone_name="Asia/Shanghai")
    client.force_login(django_user_model.objects.create_user(username="other"))
    assert client.get(reverse("mail_analysis", args=[analysis.id])).status_code == 404
    assert client.post(reverse("mail_analysis_retry", args=[analysis.id])).status_code == 404
    assert (
        client.post(
            reverse("mailbox_analyze", args=[email.mailbox_id, email.id]), {"timezone_name": "UTC"}
        ).status_code
        == 404
    )
    assert (
        client.get(reverse("task_candidate", args=[TaskCandidate.objects.get().id])).status_code
        == 404
    )

"""邮件分析入口：冻结输入、复用相同分析、保存任务候选及其所有来源。"""

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from email_agent.intelligence.understanding import PROMPT_VERSION
from email_agent.models import AgentRun, AnalysisResult, EmailMessage, TaskCandidate, TaskSource
from email_agent.services.audit import record_event
from email_agent.services.mailboxes import read_message
from email_agent.workflows.locking import RunBusyError, RunFailedError


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def analysis_input_key(message_id, snapshot, provider, config):
    """生成与重试共用同一规则，密钥不参与指纹，轮换密钥不改变业务输入。"""
    return fingerprint(
        {
            "message": str(message_id),
            "source": snapshot,
            "provider": provider,
            "model": config["model"],
            "endpoint": config["base_url"],
            "options": {"temperature": config["temperature"], "extra_body": config["extra_body"]},
            "analysis_extra_body": settings.MAIL_ANALYSIS_EXTRA_BODY,
            "prompt": PROMPT_VERSION,
        }
    )


def source_snapshot(message, timezone_name):
    """只发送当前邮件，不自动扩大到整个邮箱或会话；附件仅包含元信息。"""
    limit = settings.MAIL_ANALYSIS_MAX_CHARS
    if limit <= 0:
        raise ValidationError("MAIL_ANALYSIS_MAX_CHARS 必须大于 0。")
    return {
        "subject": message.subject,
        "current_mailbox_address": message.mailbox.email_address,
        "sender": message.sender,
        "to_recipients": message.to_recipients,
        "cc_recipients": message.cc_recipients,
        "sent_at": message.sent_at.isoformat() if message.sent_at else None,
        "received_at": message.received_at.isoformat() if message.received_at else None,
        "interpretation_timezone": timezone_name,
        "body_text": message.body_text[:limit],
        "truncated": message.body_truncated or len(message.body_text) > limit,
        "attachments": message.attachments,
    }


def analyze_message(message_id, owner, *, timezone_name=None):
    """在事务外调用模型。已有正文可分析缓存，并在结果中保留准确的输入快照。"""
    zone = timezone_name or settings.TIME_ZONE
    try:
        ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValidationError(
            "请使用有效的 IANA 时区，例如 Asia/Shanghai 或 America/New_York。"
        ) from None
    provider = settings.MAIL_ANALYSIS_PROVIDER
    config = settings.MODEL_PROVIDERS.get(provider)
    if not config:
        raise ValidationError("MAIL_ANALYSIS_PROVIDER 必须是 bailian、deepseek 或 openai。")
    message = EmailMessage.objects.select_related("mailbox").get(
        pk=message_id, mailbox__owner=owner
    )
    if not message.body_loaded:
        message = read_message(message.mailbox_id, owner, message.id)
    with transaction.atomic():
        # 锁住当前邮件，串行处理重复提交；锁在模型调用前释放。
        message = EmailMessage.objects.select_for_update().get(pk=message.id, mailbox__owner=owner)
        snapshot = source_snapshot(message, zone)
        key = analysis_input_key(message.id, snapshot, provider, config)
        analysis = AnalysisResult.objects.filter(input_key=key).first()
        if analysis is None:
            run = AgentRun.objects.create(owner=owner, kind="mail_analysis")
            analysis = AnalysisResult.objects.create(
                message=message,
                run=run,
                input_key=key,
                provider=provider,
                model_name=config["model"],
                prompt_version=PROMPT_VERSION,
                timezone_name=zone,
                source_snapshot=snapshot,
            )
            record_event(
                run,
                "mail_analysis_created",
                f"analysis-created:{run.id}",
                message_id=str(message.id),
                provider=provider,
            )
    return resume_analysis(analysis.id, owner)


def resume_analysis(analysis_id, owner):
    from email_agent.workflows.mail_processing import advance_analysis

    analysis = AnalysisResult.objects.select_related("run").get(pk=analysis_id, run__owner=owner)
    try:
        advance_analysis(analysis.id)
    except RunBusyError:
        raise ValidationError("这封邮件正在分析，请稍后刷新结果页面。") from None
    except RunFailedError:
        pass
    analysis.refresh_from_db()
    return analysis


def normalized(value):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def _task_key(analysis, item):
    message = analysis.message
    owner = item.get("owner") or {}
    scope = message.provider_thread_id or message.provider_message_id
    if not owner or (
        not owner.get("email")
        and owner.get("raw") in {"你", "您", "大家", "我们", "负责人", "项目负责人"}
    ):
        # 不把两个‘负责人待确认’误认为是同一个人，模糊身份只给重复提示。
        scope = f"{scope}:owner-unresolved:{message.id}"
    occurrence = ""
    if item["recurring"]:
        # 周期实例不明确时按来源隔离，避免把两个月的同名工作合成一项。
        occurrence = item["occurrence"] or str(message.id)
    return fingerprint(
        [
            str(message.mailbox_id),
            scope,
            normalized(item["title"]),
            normalized(owner.get("email") or owner.get("raw") or ""),
            occurrence,
        ]
    )


def _deadline_identity(item):
    deadline = item.get("deadline") or {}
    return [
        deadline.get("date") or deadline.get("raw"),
        deadline.get("time"),
        deadline.get("timezone"),
    ]


def store_candidates(analysis):
    """只合并同会话中标题、负责人、周期实例都匹配的事项；改期保留差异。"""
    with transaction.atomic():
        # 不同邮件可能并发提取同一事项，账户锁让‘查找—建候选’保持一致。
        from email_agent.models import MailboxAccount

        MailboxAccount.objects.select_for_update().get(pk=analysis.message.mailbox_id)
        for index, item in enumerate(analysis.payload["tasks"]):
            if TaskSource.objects.filter(analysis=analysis, item_index=index).exists():
                continue
            candidate, created = TaskCandidate.objects.get_or_create(
                dedupe_key=_task_key(analysis, item),
                defaults={
                    "mailbox_id": analysis.message.mailbox_id,
                    "title": item["title"],
                    "details": item,
                },
            )
            conflict = not created and (
                _deadline_identity(candidate.details) != _deadline_identity(item)
                or [
                    normalized(dependency["text"])
                    for dependency in candidate.details.get("dependencies", [])
                ]
                != [normalized(dependency["text"]) for dependency in item.get("dependencies", [])]
            )
            possible = []
            if created:
                # 简单相似度仅作辅助提示，限制候选数量，不引入向量库或额外模型调用。
                for other in TaskCandidate.objects.filter(mailbox_id=candidate.mailbox_id).exclude(
                    pk=candidate.pk
                )[:100]:
                    if (
                        SequenceMatcher(
                            None, normalized(other.title), normalized(candidate.title)
                        ).ratio()
                        >= 0.82
                    ):
                        possible.append(str(other.id))
                        if len(possible) == 5:
                            break
            TaskSource.objects.create(
                analysis=analysis,
                item_index=index,
                candidate=candidate,
                details=item,
                merged=not created,
                conflict=conflict,
                possible_duplicates=possible,
            )
        record_event(
            analysis.run,
            "mail_candidates_saved",
            f"candidates:{analysis.id}",
            count=len(analysis.payload["tasks"]),
        )

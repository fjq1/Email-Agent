"""指令写信和对话改稿：复用已有审核单及版本规则，不新增发送通道。"""

from copy import deepcopy

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from email_agent.intelligence.drafting import generate_draft
from email_agent.intelligence.understanding import UnderstandingError
from email_agent.models import ActionProposal, AgentRun, DraftTurn, MailboxAccount
from email_agent.providers.base import MailError
from email_agent.services.analysis import fingerprint
from email_agent.services.audit import record_event
from email_agent.services.mailboxes import read_message
from email_agent.services.model_selection import check_task_model, proposal_model
from email_agent.services.reviews import (
    _advance,
    _new_version,
    can_edit,
    message_digest,
    validate_form,
)
from email_agent.workflows.locking import RunBusyError, run_lock


def draft_excerpt(content):
    """近期版本提供短片段供‘上一版’等指代使用，当前完整草稿始终单独传入。"""
    return {
        "to": content.get("to", []),
        "cc": content.get("cc", []),
        "subject": content.get("subject", ""),
        "body": content.get("body", "")[:2000],
        "body_truncated": len(content.get("body", "")) > 2000,
    }


def prepare_new_proposal(plan, owner, result):
    """模型结果已保存到计划后再建审核单；重试只复用同一业务唯一键。"""
    with transaction.atomic():
        account = MailboxAccount.objects.select_for_update().get(pk=plan.mailbox_id, owner=owner)
        key = f"compose:{plan.id}"
        proposal = ActionProposal.objects.filter(source_key=key, run__owner=owner).first()
        if proposal is None:
            source = {"instruction": plan.instruction, "mailbox": account.email_address}
            proposal = ActionProposal(
                mailbox=account,
                kind=ActionProposal.Kind.COMPOSE,
                source_key=key,
                preference_snapshot=plan.preference_snapshot,
                source_snapshot=source,
                source_digest=fingerprint(source),
                content=result["content"],
            )
            content = result["content"]
            proposal.content = validate_form(
                proposal,
                owner,
                {
                    **content,
                    "version": 1,
                    "to": ", ".join(content["to"]),
                    "cc": ", ".join(content["cc"]),
                },
            )
            proposal.content_hash = fingerprint(proposal.content)
            proposal.run = AgentRun.objects.create(owner=owner, kind="action_review")
            proposal.save()
            record_event(
                proposal.run,
                "proposal_created",
                f"proposal:{proposal.id}:1",
                proposal_id=str(proposal.id),
                version=1,
                actor_id=owner.pk,
            )
            DraftTurn.objects.create(
                proposal=proposal,
                request_id=plan.id,
                instruction=plan.instruction,
                base_version=0,
                result_version=1,
                status=DraftTurn.Status.SUCCEEDED,
                after=proposal.content,
                summary=result["summary"],
                provider=result["provider"],
                model_name=result["model_name"],
                usage=result["usage"],
            )
    return _advance(proposal)


def revise_draft(proposal_id, owner, *, version, instruction, request_id):
    """每轮基于当前版本改稿。请求去重和最后的版本复核共同防止重复调用或覆盖人工编辑。"""
    proposal = ActionProposal.objects.select_related("run", "mailbox", "analysis__message").get(
        pk=proposal_id, run__owner=owner
    )
    if not instruction.strip() or len(instruction) > 2000:
        raise ValidationError("请填写最多 2000 字的修改要求。")
    turn = DraftTurn.objects.filter(request_id=request_id).first()
    if turn:
        if turn.proposal_id != proposal.id:
            raise ValidationError("请求标识无效，请刷新后重新提交。")
        if turn.status == DraftTurn.Status.SUCCEEDED:
            return proposal
        raise ValidationError(turn.error_message or "上次请求尚未完成或已中断，请刷新后重新提交。")
    locked_run_id = proposal.run_id
    try:
        with run_lock(locked_run_id):
            proposal.refresh_from_db()
            if proposal.run_id != locked_run_id or str(proposal.version) != str(version):
                raise ValidationError("草稿版本已变化，请查看最新内容后再提出修改。")
            if not proposal.is_email or not can_edit(proposal):
                raise ValidationError("当前草稿不能修改；已结束的建议请先重新处理。")
            turn = DraftTurn.objects.create(
                proposal=proposal,
                request_id=request_id,
                instruction=instruction.strip(),
                base_version=proposal.version,
                before=deepcopy(proposal.content),
            )
            try:
                # 回复和新邮件都使用初稿平台；切换导航栏选项不会改变对话中的模型。
                bound_model = proposal_model(proposal)
                if not bound_model:
                    raise ValidationError("未找到此草稿的模型记录，请重新发起写信任务。")
                check_task_model(**bound_model)
                history = list(
                    proposal.draft_turns.filter(status=DraftTurn.Status.SUCCEEDED).order_by(
                        "-created_at", "-id"
                    )[:6]
                )
                source = dict(proposal.source_snapshot) if proposal.analysis_id else {}
                if source:
                    # 只向模型提供有界的来源文本，避免多轮交流重复发送超长会话。
                    source["body_truncated"] = (
                        source.get("body_truncated", False)
                        or len(source.get("body_text", "")) > settings.MAIL_ANALYSIS_MAX_CHARS
                    )
                    source["body_text"] = source.get("body_text", "")[
                        : settings.MAIL_ANALYSIS_MAX_CHARS
                    ]
                conversation = [
                    {
                        "instruction": row.instruction,
                        "summary": row.summary,
                        "version": row.result_version,
                        "draft": draft_excerpt(row.after),
                    }
                    for row in reversed(history)
                ]
                if history and history[0].before:
                    conversation[-1]["previous_draft"] = draft_excerpt(history[0].before)
                result = generate_draft(
                    provider=bound_model["provider"],
                    mode="revise",
                    instruction=instruction,
                    current=proposal.content,
                    preferences=proposal.preference_snapshot,
                    source=source,
                    history=conversation,
                )
                fresh_source = None
                if proposal.kind == ActionProposal.Kind.REPLY:
                    message = proposal.analysis.message
                    fresh_source = read_message(proposal.mailbox_id, owner, message.id)
                    if message_digest(fresh_source) != proposal.source_digest:
                        raise ValidationError(
                            "来源邮件已变化，原草稿未覆盖。请先核对最新邮件后再修改。"
                        )
                with transaction.atomic():
                    current = ActionProposal.objects.select_for_update().get(
                        pk=proposal.id, run__owner=owner
                    )
                    if (
                        current.run_id != locked_run_id
                        or current.version != turn.base_version
                        or not can_edit(current)
                    ):
                        raise ValidationError("草稿已在其他页面变化，本轮结果未覆盖当前内容。")
                    content = result["content"]
                    content = validate_form(
                        current,
                        owner,
                        {
                            **content,
                            "version": current.version,
                            "to": ", ".join(content["to"]),
                            "cc": ", ".join(content["cc"]),
                        },
                    )
                    _new_version(current, owner, content, fresh_source)
                    turn.after, turn.result_version = content, current.version
                    turn.summary, turn.usage = result["summary"], result["usage"]
                    turn.provider, turn.model_name = result["provider"], result["model_name"]
                    turn.status = DraftTurn.Status.SUCCEEDED
                    turn.save()
                    proposal = current
            except Exception as exc:
                turn.status = DraftTurn.Status.FAILED
                turn.error_message = (
                    "；".join(exc.messages)
                    if isinstance(exc, ValidationError)
                    else str(exc)
                    if isinstance(exc, (UnderstandingError, MailError))
                    else "本轮修改未完成，原草稿已保留，请稍后重新提出要求。"
                )[:400]
                turn.save(update_fields=["status", "error_message", "updated_at"])
                raise ValidationError(turn.error_message) from None
    except RunBusyError:
        raise ValidationError("此草稿正在处理，请稍后刷新。") from None
    return _advance(proposal)

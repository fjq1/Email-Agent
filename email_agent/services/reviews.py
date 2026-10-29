"""审核生命周期：模型建议转成可编辑提案，批准绑定具体版本与内容摘要。"""

from django.core.exceptions import ValidationError
from django.db import transaction

from email_agent.forms import ReplyContentForm, TaskContentForm
from email_agent.models import (
    ActionExecution,
    ActionProposal,
    AgentRun,
    AnalysisResult,
    ApprovalDecision,
    TaskCandidate,
    TaskItem,
)
from email_agent.services.analysis import fingerprint, source_snapshot
from email_agent.services.audit import record_event
from email_agent.services.mailboxes import read_message
from email_agent.workflows.locking import RunBusyError, run_lock


def message_digest(message):
    """只比较影响发送的邮件数据；缓存抓取时间变化不会使审核失效。"""
    return fingerprint(review_snapshot(message))


def review_snapshot(message):
    return {
        key: getattr(message, key)
        for key in (
            "subject",
            "sender",
            "to_recipients",
            "cc_recipients",
            "reply_to",
            "references",
            "internet_message_id",
            "body_text",
            "body_truncated",
        )
    }


def form_for(proposal, owner, data=None):
    initial = {**proposal.content, "version": proposal.version}
    if proposal.kind == ActionProposal.Kind.REPLY:
        initial["to"] = ", ".join(initial.get("to", []))
        initial["cc"] = ", ".join(initial.get("cc", []))
        return ReplyContentForm(data, initial=initial)
    return TaskContentForm(data, initial=initial, owner=owner)


def validate_form(proposal, owner, data):
    form = form_for(proposal, owner, data)
    if not form.is_valid():
        raise ValidationError([str(error) for errors in form.errors.values() for error in errors])
    if form.cleaned_data["version"] != proposal.version:
        raise ValidationError("这份内容已被更新，请刷新页面后重新查看。")
    content = form.content()
    if proposal.kind == ActionProposal.Kind.REPLY:
        content["from"] = proposal.analysis.message.mailbox.email_address
    return content


def _advance(proposal):
    from email_agent.workflows.review import advance_review

    try:
        advance_review(proposal.id)
    except RunBusyError:
        raise ValidationError("该审核单正在处理，请稍后刷新。") from None
    proposal.refresh_from_db()
    return proposal


def prepare_proposal(analysis_id, owner, *, candidate_id=None):
    analysis = AnalysisResult.objects.select_related("run", "message__mailbox").get(
        pk=analysis_id, run__owner=owner
    )
    if analysis.run.status != AgentRun.Status.SUCCEEDED:
        raise ValidationError("请先完成邮件分析。")
    candidate = None
    digest = ""
    snapshot = analysis.source_snapshot
    if candidate_id:
        source = (
            analysis.task_sources.select_related("candidate")
            .filter(candidate_id=candidate_id)
            .first()
        )
        if source is None:
            raise TaskCandidate.DoesNotExist
        candidate, item = source.candidate, source.details
        person, deadline = item.get("owner") or {}, item.get("deadline") or {}
        content = {
            "title": item["title"],
            "description": item["description"],
            "responsible_name": person.get("raw", ""),
            "responsible_email": person.get("email") or "",
            "due_date": deadline.get("date"),
            "due_time": deadline.get("time") if deadline.get("date") else None,
            "timezone_name": deadline.get("timezone") or analysis.timezone_name,
            "dependency_notes": "\n".join(item["text"] for item in item.get("dependencies", [])),
            "dependencies": [],
        }
        kind, key = ActionProposal.Kind.TASK, f"task:{candidate.id}"
    else:
        draft = analysis.payload.get("reply_draft")
        if not draft:
            raise ValidationError("这封邮件没有可用回复草稿。")
        existing = ActionProposal.objects.filter(
            source_key=f"reply:{analysis.message_id}", run__owner=owner
        ).first()
        if existing:
            return _advance(existing)
        # 审核单创建时先刷新原文和 Reply-To，不从模型输出推测收件地址。
        message = read_message(analysis.message.mailbox_id, owner, analysis.message_id)
        # API 的 Date 可能保留 +08:00，数据库统一为 UTC；比较前统一从数据库读取。
        message.refresh_from_db()
        current = source_snapshot(message, analysis.timezone_name)
        old = analysis.source_snapshot
        for field in (
            "subject",
            "sender",
            "to_recipients",
            "cc_recipients",
            "sent_at",
            "received_at",
            "attachments",
        ):
            if current.get(field) != old.get(field):
                raise ValidationError("来源邮件已变化，请重新分析后再创建回复审核单。")
        if not (
            message.body_text.startswith(old["body_text"])
            if old["truncated"]
            else message.body_text == old["body_text"]
        ):
            raise ValidationError("来源正文已变化，请重新分析。")
        addresses = message.reply_to or [message.sender]
        content = {
            "from": message.mailbox.email_address,
            "to": [person["address"] for person in addresses if person.get("address")],
            "cc": [],
            "subject": draft["subject"],
            "body": draft["body"],
        }
        kind, key, digest = (
            ActionProposal.Kind.REPLY,
            f"reply:{message.id}",
            message_digest(message),
        )
        snapshot = review_snapshot(message)
    with transaction.atomic():
        # 以来源行串行创建，数据库唯一键再兜底，重复提醒不能重复创建正式任务。
        if candidate:
            TaskCandidate.objects.select_for_update().get(pk=candidate.id)
        else:
            type(analysis.message).objects.select_for_update().get(pk=analysis.message_id)
        proposal = ActionProposal.objects.filter(source_key=key).first()
        if proposal is None:
            run = AgentRun.objects.create(owner=owner, kind="action_review")
            proposal = ActionProposal.objects.create(
                analysis=analysis,
                candidate=candidate,
                run=run,
                kind=kind,
                source_key=key,
                content=content,
                content_hash=fingerprint(content),
                source_digest=digest,
                source_snapshot=snapshot,
            )
            record_event(
                run,
                "proposal_created",
                f"proposal:{proposal.id}:1",
                proposal_id=str(proposal.id),
                version=1,
            )
    return _advance(proposal)


def execution_for(proposal):
    return proposal.executions.filter(version=proposal.version).first()


def can_edit(proposal):
    execution = execution_for(proposal)
    return (execution is None and proposal.review_state != ActionProposal.ReviewState.APPROVED) or (
        execution is not None and execution.status == ActionExecution.Status.FAILED
    )


def save_proposal(proposal_id, owner, data):
    proposal = ActionProposal.objects.select_related("run", "analysis__message__mailbox").get(
        pk=proposal_id, run__owner=owner
    )
    source = None
    if proposal.kind == ActionProposal.Kind.REPLY:
        message = proposal.analysis.message
        source = read_message(message.mailbox_id, owner, message.id)
    locked_run_id = proposal.run_id
    try:
        with run_lock(proposal.run_id), transaction.atomic():
            proposal = ActionProposal.objects.select_for_update().get(
                pk=proposal.id, run__owner=owner
            )
            if proposal.run_id != locked_run_id:
                raise ValidationError("审核单已更新，请刷新页面。")
            if not can_edit(proposal):
                raise ValidationError("该动作已经确认、执行或结果待核对，不能直接修改。")
            content = validate_form(proposal, owner, data)
            # 每次保存生成新的审核运行，旧版本的中断和审批凭据只作为历史保留。
            old_run = proposal.run
            run = AgentRun.objects.create(owner=owner, kind="action_review")
            old_run.status = AgentRun.Status.REJECTED
            old_run.result = {**old_run.result, "superseded_by": str(run.id)}
            old_run.save(update_fields=["status", "result", "updated_at"])
            proposal.run, proposal.version = run, proposal.version + 1
            proposal.content, proposal.content_hash = content, fingerprint(content)
            if source:
                proposal.source_digest, proposal.source_snapshot = (
                    message_digest(source),
                    review_snapshot(source),
                )
            proposal.review_state = ActionProposal.ReviewState.PENDING
            proposal.save()
            record_event(
                run,
                "proposal_saved",
                f"proposal:{proposal.id}:{proposal.version}",
                proposal_id=str(proposal.id),
                actor_id=owner.pk,
                version=proposal.version,
            )
    except RunBusyError:
        raise ValidationError("该审核单正在处理，请稍后再修改。") from None
    return _advance(proposal)


def decide_proposal(proposal_id, owner, version, decision):
    proposal = ActionProposal.objects.select_related("run").get(pk=proposal_id, run__owner=owner)
    if decision not in {"approve", "reject", "cancel"}:
        raise ValidationError("请选择确认、拒绝或取消。")
    state = {"approve": "approved", "reject": "rejected", "cancel": "cancelled"}[decision]
    locked_run_id = proposal.run_id
    try:
        with run_lock(proposal.run_id), transaction.atomic():
            proposal = (
                ActionProposal.objects.select_for_update()
                .select_related("analysis__message__mailbox")
                .get(pk=proposal.id, run__owner=owner)
            )
            if proposal.run_id != locked_run_id:
                raise ValidationError("审核单已更新，请刷新页面。")
            if str(proposal.version) != str(version):
                raise ValidationError("页面版本已过期，请刷新并核对最新内容。")
            previous = proposal.decisions.filter(version=proposal.version).first()
            if previous:
                if previous.decision != state:
                    raise ValidationError("当前版本已有决定；如需重新处理，请修改内容后重新审核。")
            else:
                if proposal.review_state != ActionProposal.ReviewState.PENDING:
                    raise ValidationError("该版本不在待审核状态。")
                if fingerprint(proposal.content) != proposal.content_hash:
                    raise ValidationError("保存内容的摘要不符，请重新保存后审核。")
                if decision == "approve":
                    initial = dict(proposal.content, version=proposal.version)
                    if proposal.kind == ActionProposal.Kind.REPLY:
                        initial.update(to=", ".join(initial["to"]), cc=", ".join(initial["cc"]))
                        if "【待确认" in initial["body"] or "【待确认" in initial["subject"]:
                            raise ValidationError("草稿仍包含【待确认】占位内容，请修改后再发送。")
                        account = proposal.analysis.message.mailbox
                        if not account.can_send or account.status != "connected":
                            raise ValidationError("请先为该邮箱启用发送权限，再确认发送。")
                    validate_form(proposal, owner, initial)
                ApprovalDecision.objects.create(
                    proposal=proposal,
                    run=proposal.run,
                    actor=owner,
                    version=proposal.version,
                    decision=state,
                    content_hash=proposal.content_hash,
                    source_digest=proposal.source_digest,
                    snapshot=proposal.content,
                    source_snapshot=proposal.source_snapshot,
                )
                proposal.review_state = state
                proposal.save(update_fields=["review_state", "updated_at"])
                record_event(
                    proposal.run,
                    "proposal_decided",
                    f"decision:{proposal.id}:{proposal.version}",
                    actor_id=owner.pk,
                    decision=state,
                    version=proposal.version,
                    content_hash=proposal.content_hash,
                )
    except RunBusyError:
        raise ValidationError("该动作正在处理，请稍后刷新结果。") from None
    return _advance(proposal)


def resume_proposal(proposal_id, owner):
    proposal = ActionProposal.objects.select_related("run").get(pk=proposal_id, run__owner=owner)
    return _advance(proposal)


def update_task_status(task_id, owner, version, status):
    with transaction.atomic():
        task = TaskItem.objects.select_for_update().get(pk=task_id, owner=owner)
        if str(task.version) != str(version) or status not in TaskItem.Status.values:
            raise ValidationError("待办版本或状态无效，请刷新页面。")
        previous = task.status
        task.status, task.version = status, task.version + 1
        task.save(update_fields=["status", "version", "updated_at"])
        record_event(
            task.execution.proposal.run,
            "task_status_changed",
            f"task-status:{task.id}:{task.version}",
            actor_id=owner.pk,
            task_id=str(task.id),
            previous=previous,
            status=status,
        )
    return task

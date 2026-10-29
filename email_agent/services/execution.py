"""只执行已批准快照；数据库动作原子提交，外部发送先记账再请求且不盲重试。"""

from django.db import transaction
from django.utils import timezone

from email_agent.models import (
    ActionAttempt,
    ActionExecution,
    ActionProposal,
    ApprovalDecision,
    TaskItem,
)
from email_agent.providers import get_provider
from email_agent.providers.base import MailError, SendOutcomeUnknown
from email_agent.services.analysis import fingerprint
from email_agent.services.audit import record_event
from email_agent.services.mailboxes import _token, read_message
from email_agent.services.reviews import message_digest, validate_form


def _finish(execution, attempt, status, *, result=None, error=None):
    with transaction.atomic():
        execution.status = status
        execution.result = result or {}
        execution.last_error_code = error.code if error else ""
        execution.error_message = str(error)[:400] if error else ""
        execution.save()
        attempt.status, attempt.error_code = status, execution.last_error_code
        attempt.summary, attempt.finished_at = execution.result, timezone.now()
        attempt.save()
        record_event(
            execution.approval.run,
            "action_attempt_finished",
            f"attempt:{execution.id}:{attempt.number}",
            execution_id=str(execution.id),
            attempt=attempt.number,
            status=status,
            error_code=execution.last_error_code,
        )
    return execution


def execute_approved(proposal, decision_id):
    # 即使调用方持有旧对象，也要以数据库的当前运行、版本和审核状态为准。
    proposal = ActionProposal.objects.select_related("run", "analysis__message__mailbox").get(
        pk=proposal.pk,
        run_id=proposal.run_id,
        version=proposal.version,
        review_state=ActionProposal.ReviewState.APPROVED,
    )
    approval = ApprovalDecision.objects.get(
        pk=decision_id,
        proposal=proposal,
        version=proposal.version,
        decision=ActionProposal.ReviewState.APPROVED,
        run=proposal.run,
    )
    if (
        approval.content_hash != proposal.content_hash
        or fingerprint(approval.snapshot) != approval.content_hash
        or approval.source_digest != proposal.source_digest
    ):
        raise ValueError("审核内容摘要不一致。")
    owner, content = approval.actor, approval.snapshot
    execution, _ = ActionExecution.objects.get_or_create(
        proposal=proposal,
        version=proposal.version,
        defaults={"approval": approval, "status": ActionExecution.Status.FAILED},
    )
    if execution.status in {
        ActionExecution.Status.SUCCEEDED,
        ActionExecution.Status.ACCEPTED,
        ActionExecution.Status.UNKNOWN,
    }:
        return execution
    if execution.status == ActionExecution.Status.EXECUTING:
        # 执行锁已重新取得，但旧请求没有结果：进程可能在发出 POST 后退出，绝不能再发。
        execution.status = ActionExecution.Status.UNKNOWN
        execution.last_error_code = "interrupted_send"
        execution.error_message = "上次发送被中断，结果需要到原邮箱核对；禁止再次发送。"
        execution.save()
        execution.attempts.filter(status=ActionExecution.Status.EXECUTING).update(
            status=ActionExecution.Status.UNKNOWN,
            error_code="interrupted_send",
            finished_at=timezone.now(),
        )
        return execution
    attempt = ActionAttempt.objects.create(
        execution=execution,
        number=execution.attempts.count() + 1,
        status=ActionExecution.Status.EXECUTING,
    )
    posted = False
    try:
        # 不依赖 Web 层已经做过的校验，恢复或命令调用也必须满足同样约束。
        data = {**content, "version": proposal.version}
        if proposal.kind == ActionProposal.Kind.REPLY:
            data.update(to=", ".join(content["to"]), cc=", ".join(content["cc"]))
        validate_form(proposal, owner, data)
        if proposal.kind == ActionProposal.Kind.TASK:
            with transaction.atomic():
                values = {
                    key: content.get(key)
                    for key in (
                        "title",
                        "description",
                        "responsible_name",
                        "responsible_email",
                        "due_date",
                        "due_time",
                        "timezone_name",
                        "dependency_notes",
                    )
                }
                task, created = TaskItem.objects.get_or_create(
                    candidate=proposal.candidate,
                    defaults={"owner": owner, "execution": execution, **values},
                )
                if created:
                    task.dependencies.set(
                        TaskItem.objects.filter(owner=owner, pk__in=content["dependencies"])
                    )
                # 任务、关系、结果在同一事务中提交，重放只会返回同一个任务。
                return _finish(
                    execution,
                    attempt,
                    ActionExecution.Status.SUCCEEDED,
                    result={"task_id": str(task.id), "created": created},
                )
        message = proposal.analysis.message
        source = read_message(message.mailbox_id, owner, message.id)
        if message_digest(source) != approval.source_digest:
            raise MailError(
                "来源邮件已变化，未发送。请核对来源后重新编辑并审核。", "source_changed"
            )
        if content["from"] != source.mailbox.email_address:
            raise MailError("发件账户信息已变化，请重新编辑并审核。", "sender_changed")
        token = _token(
            source.mailbox_id,
            owner,
            force_refresh=execution.last_error_code == "access_token_expired",
            allow_send=True,
        )
        # 发送中标记必须在 POST 前提交；从这一行之后的未知异常都按可能已发送处理。
        execution.status = ActionExecution.Status.EXECUTING
        execution.error_message = ""
        execution.save(update_fields=["status", "error_message", "updated_at"])
        posted = True
        result = get_provider(source.mailbox.provider).send_reply(
            token, source, content, execution.id
        )
        status = (
            ActionExecution.Status.ACCEPTED
            if source.mailbox.provider == "outlook"
            else ActionExecution.Status.SUCCEEDED
        )
        return _finish(execution, attempt, status, result=result)
    except SendOutcomeUnknown as exc:
        return _finish(execution, attempt, ActionExecution.Status.UNKNOWN, error=exc)
    except MailError as exc:
        return _finish(execution, attempt, ActionExecution.Status.FAILED, error=exc)
    except Exception:
        # 不把 SDK/数据库异常原文写进页面；其中可能包含授权头或邮件内容。
        error = (
            SendOutcomeUnknown()
            if posted
            else MailError("执行前校验或保存失败，尚未发送，可重试。", "execution_preflight_failed")
        )
        return _finish(
            execution,
            attempt,
            ActionExecution.Status.UNKNOWN if posted else ActionExecution.Status.FAILED,
            error=error,
        )

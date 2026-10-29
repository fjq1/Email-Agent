"""指令计划的同步执行：检索、逐小批分析、冻结预览，再复用已有逐项审核。"""

from contextlib import contextmanager
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from email_agent.command_forms import CommandSpecForm, clean_zone
from email_agent.intelligence.commands import CommandSpec, parse_command
from email_agent.intelligence.drafting import generate_draft
from email_agent.intelligence.understanding import UnderstandingError
from email_agent.models import (
    ActionExecution,
    AgentRun,
    CommandAction,
    CommandItem,
    CommandPlan,
    UserPreference,
)
from email_agent.providers.base import MailError
from email_agent.services.analysis import analyze_message
from email_agent.services.audit import record_event
from email_agent.services.command_status import reconcile_status, set_status
from email_agent.services.drafts import prepare_new_proposal
from email_agent.services.mailboxes import read_message, search_messages
from email_agent.services.model_selection import check_task_model, selected_provider
from email_agent.services.preferences import effective_preferences, save_preference
from email_agent.services.reviews import check_approvable, decide_proposal, prepare_proposal
from email_agent.services.search import build_query, matches_metadata
from email_agent.workflows.locking import RunBusyError, run_lock

SCAN_LIMIT = 30
CHUNK_SIZE = 2


def reject_plan(plan):
    """模型明确拒绝的指令直接结束，原因保留在 spec，不允许改成其他操作继续执行。"""
    plan.native_query = ""
    set_status(plan, CommandPlan.Status.CANCELLED)
    record_event(
        plan.run, "command_rejected", f"command-rejected:{plan.id}", actor_id=plan.run.owner_id
    )
    return plan


@contextmanager
def locked_plan(plan_id, owner):
    plan = CommandPlan.objects.select_related("run", "mailbox").get(pk=plan_id, run__owner=owner)
    try:
        with run_lock(plan.run_id):
            plan.refresh_from_db()
            if plan.spec.get("action") == "unsupported":
                if plan.status != CommandPlan.Status.CANCELLED:
                    reject_plan(plan)
                raise ValidationError("本次指令未能处理，已取消后续处理，请重新下达新指令。")
            if plan.status == CommandPlan.Status.CANCELLED:
                raise ValidationError("此指令已取消；可以新建另一份计划。")
            yield plan
    except RunBusyError:
        raise ValidationError("此指令正在处理，请稍后刷新。") from None


def safe_error(exc):
    if isinstance(exc, ValidationError):
        return "；".join(exc.messages)[:400]
    if isinstance(exc, (UnderstandingError, MailError)):
        return str(exc)[:400]
    return "本项未完成，已保留进度，请稍后重试或进入单项页面处理。"


def create_command(owner, *, mailbox, instruction, timezone_name="", submission_key):
    if mailbox.owner_id != owner.pk or mailbox.status != "connected":
        raise ValidationError("请选择自己已连接的邮箱。")
    preferences = effective_preferences(owner, mailbox)
    zone = clean_zone(
        timezone_name or preferences.get("timezone_name", {}).get("value") or settings.TIME_ZONE
    )
    if not instruction.strip() or len(instruction) > 2000:
        raise ValidationError("请填写最多2000字的指令。")
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=owner.pk)
        plan = (
            CommandPlan.objects.filter(submission_key=submission_key).select_related("run").first()
        )
        if plan and plan.run.owner_id != owner.pk:
            raise ValidationError("无效的提交标识，请重新打开指令页面。")
        created = plan is None
        if created:
            # 在用户行锁内读取默认值；计划一旦创建，后续步骤不再读取用户的新选择。
            provider = selected_provider(owner)
            run = AgentRun.objects.create(owner=owner, kind="mail_command")
            plan = CommandPlan.objects.create(
                run=run,
                mailbox=mailbox,
                submission_key=submission_key,
                instruction=instruction.strip(),
                timezone_name=zone,
                preference_snapshot=preferences,
                provider=provider,
                model_name=settings.MODEL_PROVIDERS[provider]["model"],
            )
            record_event(run, "command_created", f"command:{plan.id}", mailbox_id=str(mailbox.id))
    # 同一提交的拒绝结果直接复用，不能因重复点击再次调用模型或重新开启。
    if plan.spec.get("action") == "unsupported":
        return plan
    plan = parse_plan(plan.id, owner)
    if created and plan.spec.get("action") == "compose_email":
        return compose_plan(plan.id, owner, plan.version)
    return plan


def compose_plan(plan_id, owner, version):
    """新邮件不检索邮箱；生成结果先落库，再建立同一份待审核草稿。"""
    with locked_plan(plan_id, owner) as plan:
        require_version(plan, version)
        if plan.spec.get("action") != "compose_email":
            raise ValidationError("当前计划不是新邮件写信请求。")
        if plan.approved_manifest or plan.actions.exists():
            return plan
        try:
            set_status(plan, CommandPlan.Status.PROCESSING)
            if not plan.draft_result:
                check_task_model(plan.provider, plan.model_name)
                request = plan.spec["compose"]
                plan.draft_result = generate_draft(
                    provider=plan.provider,
                    mode="compose",
                    instruction=plan.instruction,
                    current={
                        "from": plan.mailbox.email_address,
                        "to": request["to"],
                        "cc": request["cc"],
                        "subject": request["subject"],
                        "body": "",
                    },
                    preferences=plan.preference_snapshot,
                    options=plan.spec["options"],
                )
                plan.save(update_fields=["draft_result", "updated_at"])
            proposal = prepare_new_proposal(plan, owner, plan.draft_result)
            CommandAction.objects.get_or_create(
                plan=plan,
                proposal=proposal,
                defaults={
                    "proposal_version": proposal.version,
                    "content_hash": proposal.content_hash,
                },
            )
            plan.selection_locked_at = timezone.now()
            set_status(plan, CommandPlan.Status.READY)
            reconcile_status(plan)
        except Exception as exc:
            set_status(plan, CommandPlan.Status.FAILED, safe_error(exc))
    return plan


def parse_plan(plan_id, owner):
    with locked_plan(plan_id, owner) as plan:
        if plan.spec:
            return plan
        try:
            check_task_model(plan.provider, plan.model_name)
            spec, usage = parse_command(
                plan.instruction,
                provider=plan.provider,
                mailbox=plan.mailbox,
                timezone_name=plan.timezone_name,
                current_time=timezone.now().astimezone(ZoneInfo(plan.timezone_name)),
            )
            spec = CommandSpec.model_validate(spec).model_dump(mode="json")
            plan.spec, plan.usage = spec, usage
            if spec["action"] == "unsupported":
                return reject_plan(plan)
            plan.native_query = build_query(
                plan.mailbox.provider, spec["filters"], plan.timezone_name
            )
            set_status(plan, CommandPlan.Status.PREVIEW)
        except Exception as exc:
            set_status(plan, CommandPlan.Status.FAILED, safe_error(exc))
    return plan


def plan_initial(plan):
    spec = plan.spec
    return {
        **spec.get("filters", {}),
        **spec.get("options", {}),
        "version": plan.version,
        "action": spec.get("action", "search"),
        "limit": spec.get("limit", 5),
        "timezone_name": plan.timezone_name,
        "keywords": "\n".join(spec.get("filters", {}).get("keywords", [])),
        "unread": {None: "all", True: "unread", False: "read"}[
            spec.get("filters", {}).get("unread")
        ],
    }


def save_plan(plan_id, owner, data):
    with locked_plan(plan_id, owner) as plan:
        if plan.spec.get("action") == "compose_email":
            raise ValidationError("新邮件请在草稿审核页手动或通过对话修改。")
        if plan.selection_locked_at or plan.approved_manifest:
            raise ValidationError("已开始分析的范围不能修改；请新建指令。")
        form = CommandSpecForm(data)
        if not form.is_valid():
            raise ValidationError(
                [str(error) for errors in form.errors.values() for error in errors]
            )
        if form.cleaned_data["version"] != plan.version:
            raise ValidationError("计划版本已变化，请刷新页面。")
        plan.spec, plan.timezone_name = form.spec(plan.spec), form.cleaned_data["timezone_name"]
        plan.native_query = build_query(
            plan.mailbox.provider, plan.spec["filters"], plan.timezone_name
        )
        plan.version += 1
        plan.items.all().delete()
        plan.searched_at, plan.scanned, plan.has_more = None, 0, False
        set_status(plan, CommandPlan.Status.PREVIEW)
    return plan


def require_version(plan, version):
    if str(plan.version) != str(version):
        raise ValidationError("计划版本已变化，请刷新并核对最新内容。")


def search_plan(plan_id, owner, version):
    with locked_plan(plan_id, owner) as plan:
        require_version(plan, version)
        if not plan.spec or plan.spec["action"] in {
            "unsupported",
            "preference_only",
            "compose_email",
        }:
            raise ValidationError("当前计划不能检索，请先修改范围或确认偏好。")
        if plan.searched_at or plan.selection_locked_at:
            return plan
        plan.items.all().delete()
        plan.scanned, plan.has_more = 0, False
        cursor, found = "", 0
        try:
            query = build_query(plan.mailbox.provider, plan.spec["filters"], plan.timezone_name)
            plan.native_query = query
            while plan.scanned < SCAN_LIMIT and found < plan.spec["limit"]:
                emails, next_cursor = search_messages(
                    plan.mailbox_id,
                    owner,
                    query=query,
                    page_size=min(10, SCAN_LIMIT - plan.scanned),
                    cursor=cursor,
                    folder=plan.spec["filters"]["folder"],
                )
                plan.has_more = bool(next_cursor)
                for index, email in enumerate(emails):
                    plan.scanned += 1
                    if matches_metadata(email, plan.spec["filters"], plan.timezone_name):
                        _, created = CommandItem.objects.get_or_create(plan=plan, message=email)
                        found += int(created)
                    if found >= plan.spec["limit"]:
                        plan.has_more = bool(next_cursor or index + 1 < len(emails))
                        break
                if found >= plan.spec["limit"] or not next_cursor:
                    break
                # API 可能返回空页但仍带 cursor；每次请求都计入扫描预算以保持有界。
                if not emails:
                    plan.scanned += 1
                cursor, plan.has_more = next_cursor, True
            plan.searched_at = timezone.now()
            set_status(plan, CommandPlan.Status.SEARCHED)
            record_event(
                plan.run,
                "command_searched",
                f"command-search:{plan.id}:{plan.version}",
                count=found,
                scanned=plan.scanned,
            )
        except Exception as exc:
            set_status(plan, CommandPlan.Status.FAILED, safe_error(exc))
    return plan


def selected_rows(queryset, ids):
    try:
        keys = {int(value) for value in ids}
    except (ValueError, TypeError):
        raise ValidationError("选择的项目无效。") from None
    rows = list(queryset.filter(pk__in=keys))
    if not keys or len(rows) != len(keys):
        raise ValidationError("请选择本计划中有效的项目。")
    return rows


def process_plan(plan_id, owner, version, item_ids=(), *, retry_failed=False):
    with locked_plan(plan_id, owner) as plan:
        require_version(plan, version)
        if not plan.searched_at or plan.spec["action"] not in {
            "summarize",
            "extract_tasks",
            "draft_replies",
        }:
            raise ValidationError("请先检索，并选择摘要、提取待办或生成回复。")
        if plan.approved_manifest:
            raise ValidationError("本计划已确认动作，分析结果不能再更换。")
        if not plan.selection_locked_at:
            rows = selected_rows(plan.items.all(), item_ids)
            plan.items.exclude(pk__in=[item.pk for item in rows]).update(
                status=CommandItem.Status.SKIPPED
            )
            plan.items.filter(pk__in=[item.pk for item in rows]).update(selected=True)
            plan.selection_locked_at = timezone.now()
        if retry_failed:
            plan.items.filter(selected=True, status="failed").update(
                status="found", error_message=""
            )
        set_status(plan, CommandPlan.Status.PROCESSING)
        for item in plan.items.filter(selected=True, status="found").select_related("message")[
            :CHUNK_SIZE
        ]:
            try:
                check_task_model(plan.provider, plan.model_name)
                read_message(plan.mailbox_id, owner, item.message_id)
                analysis = analyze_message(
                    item.message_id,
                    owner,
                    provider=plan.provider,
                    timezone_name=plan.timezone_name,
                    preferences=plan.preference_snapshot,
                    processing_options={**plan.spec["options"], "action": plan.spec["action"]},
                )
                item.analysis = analysis
                if analysis.run.status != AgentRun.Status.SUCCEEDED:
                    raise ValidationError(analysis.error_message or "该邮件分析尚未完成。")
                proposals = []
                if plan.spec["action"] == "extract_tasks":
                    for source in analysis.task_sources.all():
                        proposals.append(
                            prepare_proposal(analysis.id, owner, candidate_id=source.candidate_id)
                        )
                elif plan.spec["action"] == "draft_replies" and analysis.payload.get("reply_draft"):
                    proposals.append(prepare_proposal(analysis.id, owner))
                for proposal in proposals:
                    CommandAction.objects.get_or_create(
                        plan=plan,
                        proposal=proposal,
                        defaults={
                            "item": item,
                            "proposal_version": proposal.version,
                            "content_hash": proposal.content_hash,
                        },
                    )
                item.status, item.error_message = CommandItem.Status.DONE, ""
            except Exception as exc:
                item.status, item.error_message = CommandItem.Status.FAILED, safe_error(exc)
            item.save()
        if plan.items.filter(selected=True, status="found").exists():
            set_status(plan, CommandPlan.Status.PROCESSING)
        else:
            status = (
                CommandPlan.Status.READY
                if plan.actions.exists()
                else CommandPlan.Status.PARTIAL
                if plan.items.filter(status="failed").exists()
                else CommandPlan.Status.COMPLETED
            )
            set_status(plan, status)
        reconcile_status(plan)
    return plan


def refresh_actions(plan_id, owner, version):
    with locked_plan(plan_id, owner) as plan:
        require_version(plan, version)
        if plan.approved_manifest:
            raise ValidationError("批量决定已保存，不能更换已确认的版本。")
        for item in plan.actions.select_related("proposal"):
            item.proposal_version, item.content_hash = (
                item.proposal.version,
                item.proposal.content_hash,
            )
            item.save(update_fields=["proposal_version", "content_hash"])
        plan.version += 1
        plan.save(update_fields=["version", "updated_at"])
    return plan


def confirm_batch(plan_id, owner, version, action_ids=()):
    with locked_plan(plan_id, owner) as plan:
        require_version(plan, version)
        if not plan.approved_manifest:
            if plan.items.filter(selected=True, status="found").exists():
                raise ValidationError("请先完成当前所选邮件的分析。")
            rows = selected_rows(
                plan.actions.select_related(
                    "proposal__run", "proposal__analysis__message__mailbox"
                ),
                action_ids,
            )
            manifest = []
            # 在执行第一项前检查所有选择，避免最后才发现某项没有授权或已被编辑。
            for item in rows:
                proposal = item.proposal
                if (
                    proposal.version != item.proposal_version
                    or proposal.content_hash != item.content_hash
                    or proposal.review_state != "pending"
                ):
                    raise ValidationError("有审核内容已更新或不再待审核，请更新批量预览再选择。")
                check_approvable(proposal, owner)
                manifest.append(
                    {
                        "action_id": item.pk,
                        "proposal_id": str(proposal.id),
                        "version": item.proposal_version,
                        "content_hash": item.content_hash,
                    }
                )
            with transaction.atomic():
                plan.approved_manifest = manifest
                plan.version += 1
                set_status(plan, CommandPlan.Status.DISPATCHING)
                record_event(
                    plan.run,
                    "command_batch_confirmed",
                    f"command-batch:{plan.id}",
                    actor_id=owner.pk,
                    manifest=manifest,
                )
        _dispatch(plan, owner)
    return plan


def _dispatch(plan, owner):
    remaining = [
        entry
        for entry in plan.approved_manifest
        if not plan.actions.get(pk=entry["action_id"]).dispatched_at
    ]
    for entry in remaining[:CHUNK_SIZE]:
        item = plan.actions.select_related("proposal").get(pk=entry["action_id"])
        try:
            if (
                item.proposal.version != entry["version"]
                or item.proposal.content_hash != entry["content_hash"]
            ):
                raise ValidationError("内容已变化，本项未执行，请单独审核新版本。")
            proposal = decide_proposal(item.proposal_id, owner, entry["version"], "approve")
            execution = proposal.executions.filter(version=entry["version"]).first()
            if execution is None or execution.status not in {
                ActionExecution.Status.SUCCEEDED,
                ActionExecution.Status.ACCEPTED,
            }:
                raise ValidationError(
                    execution.error_message
                    if execution
                    else "尚未取得执行结果，请查看单项审核记录。"
                )
        except Exception as exc:
            item.error_message = safe_error(exc)
        item.dispatched_at = timezone.now()
        item.save(update_fields=["error_message", "dispatched_at"])
    # 其他入口可能已完成这些动作，或已手动重试成功；汇总时以账本为准。
    reconcile_status(plan)


def confirm_suggestion(plan_id, owner, version, index, expected_version):
    with locked_plan(plan_id, owner) as plan:
        require_version(plan, version)
        try:
            suggestion = plan.spec["preference_suggestions"][int(index)]
            if int(index) < 0 or suggestion["evidence"] not in plan.instruction:
                raise ValueError
        except (ValueError, KeyError, IndexError, TypeError):
            raise ValidationError("偏好提案无效。") from None
        item = save_preference(
            owner,
            key=suggestion["key"],
            value=suggestion["value"],
            mailbox=plan.mailbox,
            expected_version=expected_version,
            source=f"command:{plan.id}:{index}",
        )
        record_event(
            plan.run,
            "preference_confirmed",
            f"preference:{plan.id}:{index}:{item.version}",
            preference_id=str(item.id),
            version=item.version,
            actor_id=owner.pk,
        )
        if plan.spec.get("action") == "preference_only":
            confirmed = all(
                UserPreference.objects.filter(
                    owner=owner,
                    mailbox=plan.mailbox,
                    key=entry["key"],
                    value=entry["value"],
                    enabled=True,
                ).exists()
                for entry in plan.spec.get("preference_suggestions", [])
            )
            if confirmed:
                set_status(plan, CommandPlan.Status.COMPLETED)
    return item


def cancel_plan(plan_id, owner, version):
    with locked_plan(plan_id, owner) as plan:
        require_version(plan, version)
        plan.version += 1
        set_status(plan, CommandPlan.Status.CANCELLED)
        record_event(
            plan.run, "command_cancelled", f"command-cancelled:{plan.id}", actor_id=owner.pk
        )
    return plan

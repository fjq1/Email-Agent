"""指令与偏好的界面入口。GET 只展示，解析/检索/分析/批准/保存均使用 POST。"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import prefetch_related_objects
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from email_agent.command_forms import (
    ACTION_CHOICES,
    CommandSpecForm,
    CommandStartForm,
    PreferenceForm,
)
from email_agent.models import ActionExecution, CommandPlan, TaskItem, UserPreference
from email_agent.services import commands
from email_agent.services.command_status import display_status
from email_agent.services.preferences import (
    change_preference,
    describe_preferences,
    display_preference,
    save_preference,
)


def error_message(exc):
    return "；".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


@login_required
@require_http_methods(["GET", "POST"])
@never_cache
def index(request):
    form = CommandStartForm(request.POST if request.method == "POST" else None, owner=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            plan = commands.create_command(request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, error_message(exc))
        else:
            return redirect("command_detail", plan_id=plan.id)
    plans = list(
        CommandPlan.objects.filter(run__owner=request.user)
        .select_related("mailbox", "run")
        .prefetch_related("items", "actions__proposal__executions", "actions__proposal__run")[:20]
    )
    for plan in plans:
        display_status(plan)
    return render(
        request,
        "email_agent/commands.html",
        {
            "form": form,
            "plans": plans,
            "analysis_provider": settings.MAIL_ANALYSIS_PROVIDER,
        },
    )


def owned_plan(request, plan_id):
    return get_object_or_404(
        CommandPlan.objects.select_related("run", "mailbox"), pk=plan_id, run__owner=request.user
    )


@login_required
@require_GET
@never_cache
def detail(request, plan_id):
    plan = owned_plan(request, plan_id)
    if plan.spec.get("action") == "unsupported":
        # 拒绝结果单独展示，既不呈现无关检索条件，也不暴露后续修改和执行入口。
        reasons = plan.spec.get("clarifications") or [plan.spec.get("explanation", "")]
        reply = "\n".join(dict.fromkeys(reason.strip() for reason in reasons if reason.strip()))
        return render(
            request,
            "email_agent/command_rejected.html",
            {
                "plan": display_status(plan),
                "refusal_reply": reply or "当前信息不足或请求暂不支持，本次未能继续处理。",
            },
        )
    prefetch_related_objects(
        [plan], "items", "actions__proposal__executions", "actions__proposal__run"
    )
    display_status(plan)
    actions = list(plan.actions.select_related("proposal__run", "proposal__analysis"))
    confirmed_versions = {entry["action_id"]: entry["version"] for entry in plan.approved_manifest}
    for action in actions:
        proposal = action.proposal
        action.stale = proposal.review_state == "pending" and (
            action.proposal_version != proposal.version
            or action.content_hash != proposal.content_hash
        )
        action.selectable = (
            not action.stale and proposal.review_state == "pending" and not plan.approved_manifest
        )
        # 未经过批量确认时，展示单项审核的实际当前版本，不能继续查旧预览的执行记录。
        action.execution_version = confirmed_versions.get(action.pk, proposal.version)
        action.execution = proposal.executions.filter(version=action.execution_version).first()
        if action.execution and action.execution.status in {
            ActionExecution.Status.SUCCEEDED,
            ActionExecution.Status.ACCEPTED,
        }:
            action.error_message = ""  # 手动重试已成功时，不继续显示批量执行的旧失败提示。
        action.dependencies = TaskItem.objects.filter(
            owner=request.user, pk__in=proposal.content.get("dependencies", [])
        )
        action.reused = (
            action.item_id is not None and proposal.analysis_id != action.item.analysis_id
        )
    suggestions = []
    for index, suggestion in enumerate(plan.spec.get("preference_suggestions", [])):
        existing = UserPreference.objects.filter(
            owner=request.user, mailbox=plan.mailbox, key=suggestion["key"]
        ).first()
        suggestions.append(
            {
                **suggestion,
                **display_preference(suggestion["key"], suggestion["value"]),
                "index": index,
                "expected_version": existing.version if existing else 0,
                "confirmed": existing is not None
                and existing.confirmation_source == f"command:{plan.id}:{index}"
                and existing.enabled
                and existing.value == suggestion["value"],
            }
        )
    return render(
        request,
        "email_agent/command_detail.html",
        {
            "plan": plan,
            "items": plan.items.select_related("message", "analysis__run"),
            "actions": actions,
            "suggestions": suggestions,
            "action_label": dict(ACTION_CHOICES).get(plan.spec.get("action")),
            "preference_display": describe_preferences(plan.preference_snapshot),
            "request_style": [
                display_preference(key, value)
                for key, value in plan.spec.get("options", {}).items()
                if value
            ],
            "scan_limit": commands.SCAN_LIMIT,
            "chunk_size": commands.CHUNK_SIZE,
            "has_pending_items": plan.items.filter(selected=True, status="found").exists(),
            "has_failed_items": plan.items.filter(selected=True, status="failed").exists(),
            "has_selectable_actions": any(item.selectable for item in actions),
            "allow_edit": bool(plan.spec)
            and plan.spec.get("action") != "compose_email"
            and not plan.selection_locked_at
            and plan.status != "cancelled",
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
@never_cache
def edit(request, plan_id):
    plan = owned_plan(request, plan_id)
    if plan.spec.get("action") == "unsupported":
        return redirect("command_detail", plan_id=plan.id)
    form = CommandSpecForm(
        request.POST if request.method == "POST" else None, initial=commands.plan_initial(plan)
    )
    if request.method == "POST" and form.is_valid():
        try:
            commands.save_plan(plan.id, request.user, request.POST)
        except ValidationError as exc:
            form.add_error(None, error_message(exc))
        else:
            return redirect("command_detail", plan_id=plan.id)
    return render(request, "email_agent/command_edit.html", {"plan": plan, "form": form})


@login_required
@require_POST
@never_cache
def act(request, plan_id, operation):
    plan = owned_plan(request, plan_id)
    if plan.spec.get("action") == "unsupported":
        return redirect("command_detail", plan_id=plan.id)
    version = request.POST.get("version")
    try:
        if operation == "parse":
            plan = commands.parse_plan(plan_id, request.user)
            if plan.spec.get("action") == "compose_email":
                commands.compose_plan(plan_id, request.user, plan.version)
        elif operation == "compose":
            commands.compose_plan(plan_id, request.user, version)
        elif operation == "search":
            commands.search_plan(plan_id, request.user, version)
        elif operation in {"process", "retry"}:
            commands.process_plan(
                plan_id,
                request.user,
                version,
                request.POST.getlist("item_ids"),
                retry_failed=operation == "retry",
            )
        elif operation == "refresh":
            commands.refresh_actions(plan_id, request.user, version)
        elif operation in {"approve", "continue"}:
            if operation == "continue" and not owned_plan(request, plan_id).approved_manifest:
                raise ValidationError("请先核对并确认具体动作。")
            commands.confirm_batch(
                plan_id, request.user, version, request.POST.getlist("action_ids")
            )
        elif operation == "preference":
            commands.confirm_suggestion(
                plan_id,
                request.user,
                version,
                request.POST.get("index"),
                request.POST.get("expected_version"),
            )
            messages.success(request, "已明确保存此邮箱的偏好；对之后新生成的计划和分析生效。")
        elif operation == "cancel":
            commands.cancel_plan(plan_id, request.user, version)
        else:
            raise ValidationError("无效操作。")
    except ValidationError as exc:
        messages.error(request, error_message(exc))
    return redirect("command_detail", plan_id=plan_id)


@login_required
@require_http_methods(["GET", "POST"])
@never_cache
def preferences(request, preference_id=None):
    item = (
        get_object_or_404(UserPreference, pk=preference_id, owner=request.user)
        if preference_id
        else None
    )
    key = item.key if item else request.GET.get("key", "timezone_name")
    if key not in UserPreference.Key.values:
        key = "timezone_name"
    form = PreferenceForm(
        request.POST if request.method == "POST" else None,
        owner=request.user,
        item=item,
        initial={"key": key},
    )
    if request.method == "POST" and form.is_valid():
        try:
            data = form.cleaned_data
            save_preference(
                request.user,
                key=data["key"],
                value=data["value"],
                mailbox=data["mailbox"],
                expected_version=data["version"],
            )
        except ValidationError as exc:
            form.add_error(None, error_message(exc))
        else:
            messages.success(request, "偏好已确认保存并启用。")
            return redirect("preferences")
    return render(
        request,
        "email_agent/preferences.html",
        {
            "form": form,
            "item": item,
            "selected_key": key,
            "key_choices": UserPreference.Key.choices,
            "preferences": UserPreference.objects.filter(owner=request.user).select_related(
                "mailbox"
            ),
        },
    )


@login_required
@require_POST
@never_cache
def preference_action(request, preference_id):
    get_object_or_404(UserPreference, pk=preference_id, owner=request.user)
    try:
        change_preference(
            preference_id, request.user, request.POST.get("version"), request.POST.get("operation")
        )
    except ValidationError as exc:
        messages.error(request, error_message(exc))
    return redirect("preferences")

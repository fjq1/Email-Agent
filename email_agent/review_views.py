"""人工审核页面：GET 仅展示，所有修改、决定和执行都通过 CSRF 保护的 POST。"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from email_agent.models import (
    ActionExecution,
    ActionProposal,
    AgentRun,
    AnalysisResult,
    TaskCandidate,
    TaskItem,
)
from email_agent.providers.base import MailError
from email_agent.services.reviews import (
    can_edit,
    decide_proposal,
    execution_for,
    form_for,
    prepare_proposal,
    resume_proposal,
    save_proposal,
    update_task_status,
)


def owned_proposal(request, proposal_id):
    return get_object_or_404(
        ActionProposal.objects.select_related("run", "analysis__message__mailbox", "candidate"),
        pk=proposal_id,
        run__owner=request.user,
    )


def show_error(request, exc):
    messages.error(
        request, "；".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
    )


@login_required
@require_POST
@never_cache
def prepare(request, analysis_id):
    get_object_or_404(AnalysisResult, pk=analysis_id, run__owner=request.user)
    try:
        proposal = prepare_proposal(
            analysis_id, request.user, candidate_id=request.POST.get("candidate_id") or None
        )
    except TaskCandidate.DoesNotExist:
        raise Http404 from None
    except (ValidationError, MailError) as exc:
        show_error(request, exc)
        return redirect("mail_analysis", analysis_id=analysis_id)
    return redirect("action_review", proposal_id=proposal.id)


@login_required
@require_GET
@never_cache
def review_list(request):
    proposals = ActionProposal.objects.filter(run__owner=request.user).select_related(
        "analysis__message", "run"
    )
    return render(
        request,
        "email_agent/review_list.html",
        {"page": Paginator(proposals, 20).get_page(request.GET.get("page"))},
    )


@login_required
@require_GET
@never_cache
def detail(request, proposal_id):
    proposal = owned_proposal(request, proposal_id)
    execution = execution_for(proposal)
    retryable = execution is None or execution.status == ActionExecution.Status.FAILED
    return render(
        request,
        "email_agent/review_detail.html",
        {
            "proposal": proposal,
            "execution": execution,
            "editable": can_edit(proposal),
            "show_retry": retryable
            and proposal.run.status
            in {AgentRun.Status.FAILED, AgentRun.Status.RUNNING, AgentRun.Status.PENDING},
            "attempts": execution.attempts.all() if execution else [],
            "dependencies": TaskItem.objects.filter(
                owner=request.user, pk__in=proposal.content.get("dependencies", [])
            ),
            "decisions": proposal.decisions.select_related("actor").order_by("-version"),
            "source_item": proposal.analysis.task_sources.filter(
                candidate_id=proposal.candidate_id
            ).first()
            if proposal.candidate_id
            else None,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
@never_cache
def edit(request, proposal_id):
    proposal = owned_proposal(request, proposal_id)
    if not can_edit(proposal):
        messages.error(request, "已执行或待核对的动作不能修改。")
        return redirect("action_review", proposal_id=proposal.id)
    form = form_for(proposal, request.user, request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            save_proposal(proposal.id, request.user, request.POST)
        except (ValidationError, MailError) as exc:
            form.add_error(
                None, "；".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            )
        else:
            messages.success(request, "修改已保存为新版本，请核对预览后确认。")
            return redirect("action_review", proposal_id=proposal.id)
    return render(request, "email_agent/review_edit.html", {"proposal": proposal, "form": form})


@login_required
@require_POST
@never_cache
def decide(request, proposal_id):
    owned_proposal(request, proposal_id)
    try:
        decide_proposal(
            proposal_id, request.user, request.POST.get("version"), request.POST.get("decision")
        )
    except ValidationError as exc:
        show_error(request, exc)
    return redirect("action_review", proposal_id=proposal_id)


@login_required
@require_POST
@never_cache
def resume(request, proposal_id):
    owned_proposal(request, proposal_id)
    try:
        resume_proposal(proposal_id, request.user)
    except ValidationError as exc:
        show_error(request, exc)
    return redirect("action_review", proposal_id=proposal_id)


@login_required
@require_GET
@never_cache
def task_list(request):
    tasks = TaskItem.objects.filter(owner=request.user).select_related("candidate__mailbox")
    return render(
        request,
        "email_agent/tasks.html",
        {"page": Paginator(tasks, 20).get_page(request.GET.get("page"))},
    )


@login_required
@require_http_methods(["GET", "POST"])
@never_cache
def task_detail(request, task_id):
    task = get_object_or_404(
        TaskItem.objects.select_related("candidate", "execution__proposal"),
        pk=task_id,
        owner=request.user,
    )
    if request.method == "POST":
        try:
            update_task_status(
                task.id, request.user, request.POST.get("version"), request.POST.get("status")
            )
        except ValidationError as exc:
            show_error(request, exc)
        else:
            messages.success(request, "待办状态已更新。")
        return redirect("task_detail", task_id=task.id)
    return render(
        request,
        "email_agent/task_detail.html",
        {"task": task, "status_choices": TaskItem.Status.choices},
    )

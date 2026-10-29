"""邮件分析与候选展示：模型调用仅由 POST 显式触发，读取结果不再次调用模型。"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from email_agent.forms import MailAnalysisForm
from email_agent.models import AgentRun, AnalysisResult, EmailMessage, TaskCandidate
from email_agent.providers.base import MailError
from email_agent.services.analysis import analyze_message, resume_analysis


@login_required
@require_POST
@never_cache
def start(request, account_id, message_id):
    email = get_object_or_404(
        EmailMessage, pk=message_id, mailbox_id=account_id, mailbox__owner=request.user
    )
    form = MailAnalysisForm(request.POST)
    if not form.is_valid():
        messages.error(
            request, "；".join(str(error) for errors in form.errors.values() for error in errors)
        )
        return redirect("mailbox_message", account_id=account_id, message_id=message_id)
    try:
        analysis = analyze_message(email.id, request.user, **form.cleaned_data)
    except (ValidationError, MailError) as exc:
        messages.error(
            request, "；".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        )
        return redirect("mailbox_message", account_id=account_id, message_id=message_id)
    return redirect("mail_analysis", analysis_id=analysis.id)


@login_required
@require_GET
@never_cache
def detail(request, analysis_id):
    analysis = get_object_or_404(
        AnalysisResult.objects.select_related("run", "message__mailbox"),
        pk=analysis_id,
        run__owner=request.user,
    )
    return render(
        request,
        "email_agent/analysis_detail.html",
        {
            "analysis": analysis,
            "task_sources": analysis.task_sources.select_related("candidate"),
        },
    )


@login_required
@require_POST
@never_cache
def retry(request, analysis_id):
    get_object_or_404(AnalysisResult, pk=analysis_id, run__owner=request.user)
    try:
        resume_analysis(analysis_id, request.user)
    except ValidationError as exc:
        messages.info(request, "；".join(exc.messages))
    return redirect("mail_analysis", analysis_id=analysis_id)


@login_required
@require_GET
@never_cache
def candidate_list(request):
    candidates = TaskCandidate.objects.filter(mailbox__owner=request.user).select_related("mailbox")
    return render(
        request,
        "email_agent/candidates.html",
        {"page": Paginator(candidates, 20).get_page(request.GET.get("page"))},
    )


@login_required
@require_GET
@never_cache
def candidate_detail(request, candidate_id):
    candidate = get_object_or_404(
        TaskCandidate.objects.select_related("mailbox"),
        pk=candidate_id,
        mailbox__owner=request.user,
    )
    sources = candidate.sources.filter(
        analysis__run__status=AgentRun.Status.SUCCEEDED
    ).select_related("analysis__message")
    return render(
        request, "email_agent/candidate_detail.html", {"candidate": candidate, "sources": sources}
    )

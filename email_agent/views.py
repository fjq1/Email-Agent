"""阶段 0 控制台：登录、所属运行和基础设施诊断。"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from email_agent.intelligence.models import provider_statuses
from email_agent.models import AgentRun, MailboxAccount
from email_agent.services.health import infrastructure_status
from email_agent.services.oauth import connection_options
from email_agent.services.runs import create_probe, decide_probe, resume_probe


@require_GET
def liveness(request):
    return JsonResponse({"status": "alive"})


@require_GET
def readiness(request):
    checks = infrastructure_status()
    ready = all(check["ok"] for check in checks.values())
    # 公开探针只返回总体状态，具体故障由管理命令查看。
    return JsonResponse(
        {"status": "ready" if ready else "unavailable"}, status=200 if ready else 503
    )


@login_required
@require_GET
@never_cache
def dashboard(request):
    return render(
        request,
        "email_agent/dashboard.html",
        {
            "runs": AgentRun.objects.filter(owner=request.user)[:20],
            "providers": provider_statuses(),
            "mailboxes": MailboxAccount.objects.filter(owner=request.user),
            "mail_connections": connection_options(),
        },
    )


@login_required
@require_POST
def start_probe(request):
    run = create_probe(request.user)
    notify_result(request, run)
    return redirect("dashboard")


@login_required
@require_POST
def review_probe(request, run_id):
    try:
        run = decide_probe(run_id, request.user, request.POST.get("decision", ""))
    except AgentRun.DoesNotExist:
        raise Http404 from None
    except ValidationError as exc:
        messages.error(request, "；".join(exc.messages))
    else:
        notify_result(request, run)
    return redirect("dashboard")


@login_required
@require_POST
def resume_run(request, run_id):
    try:
        run = resume_probe(run_id, request.user)
    except AgentRun.DoesNotExist:
        raise Http404 from None
    except ValidationError as exc:
        messages.error(request, "；".join(exc.messages))
    else:
        notify_result(request, run)
    return redirect("dashboard")


def notify_result(request, run):
    """处理结果在当前请求中产生，返回页面时直接告知用户。"""
    if run.status == AgentRun.Status.FAILED:
        messages.error(request, f"诊断处理失败（{run.last_error_code}），可以手动重试。")
    elif run.status == AgentRun.Status.WAITING_REVIEW:
        messages.success(request, "诊断已准备完成，等待你确认或拒绝。")
    elif run.status == AgentRun.Status.REJECTED:
        messages.success(request, "已拒绝本次诊断。")
    elif run.status == AgentRun.Status.SUCCEEDED:
        messages.success(request, "诊断处理完成，结果已保存。")

"""阶段 0 控制台：登录、所属运行和基础设施诊断。"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from email_agent.intelligence.models import provider_statuses
from email_agent.models import AgentRun
from email_agent.services.health import infrastructure_status
from email_agent.services.runs import create_probe, decide_probe, retry_probe


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
def dashboard(request):
    return render(
        request,
        "email_agent/dashboard.html",
        {
            "runs": AgentRun.objects.filter(owner=request.user)[:20],
            "providers": provider_statuses(),
        },
    )


@login_required
@require_POST
def start_probe(request):
    create_probe(request.user)
    messages.success(request, "诊断流程已保存，后台将执行并暂停等待审核。")
    return redirect("dashboard")


@login_required
@require_POST
def review_probe(request, run_id):
    try:
        decide_probe(run_id, request.user, request.POST.get("decision", ""))
    except AgentRun.DoesNotExist:
        raise Http404 from None
    except ValidationError as exc:
        messages.error(request, "；".join(exc.messages))
    return redirect("dashboard")


@login_required
@require_POST
def retry_run(request, run_id):
    try:
        retry_probe(run_id, request.user)
    except AgentRun.DoesNotExist:
        raise Http404 from None
    except ValidationError as exc:
        messages.error(request, "；".join(exc.messages))
    return redirect("dashboard")

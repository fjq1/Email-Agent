"""真实邮箱页面：连接、只读检索、邮件详情和会话分页。"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from email_agent.forms import MailSearchForm
from email_agent.models import EmailMessage, MailboxAccount
from email_agent.providers.base import MailError
from email_agent.services.mailboxes import read_message, read_thread, search_messages
from email_agent.services.oauth import (
    begin_connection,
    connection_options,
    disconnect_mailbox,
    finish_connection,
)


@login_required
@require_GET
@never_cache
def setup(request):
    return render(request, "email_agent/mailbox_setup.html", {"connections": connection_options()})


@login_required
@require_POST
@never_cache
def connect(request, provider):
    if not request.session.session_key:
        request.session.save()
    try:
        url = begin_connection(
            request.user,
            request.session.session_key,
            provider,
            request.get_host(),
            request.POST.get("account_id") or None,
        )
    except (MailboxAccount.DoesNotExist, ValidationError, ValueError):
        raise Http404 from None
    except MailError as exc:
        messages.error(request, str(exc))
        return redirect("mailbox_setup")
    return redirect(url)


@require_GET
@never_cache
def callback(request, provider):
    # 登录过期时直接回登录页，避免把授权码拼进登录页的 next 参数。
    if not request.user.is_authenticated or not request.session.session_key:
        response = redirect("login")
        response["Referrer-Policy"] = "no-referrer"
        return response
    try:
        account = finish_connection(
            request.user, request.session.session_key, provider, request.GET.dict()
        )
    except (MailboxAccount.DoesNotExist, ValidationError, ValueError):
        raise Http404 from None
    except MailError as exc:
        messages.error(request, str(exc))
        response = redirect("dashboard")
    else:
        messages.success(request, f"已连接 {account.email_address}，可以检索和读取邮件。")
        response = redirect("mailbox_messages", account_id=account.id)
    response["Referrer-Policy"] = "no-referrer"
    return response


@login_required
@require_POST
@never_cache
def disconnect(request, account_id):
    try:
        account = disconnect_mailbox(account_id, request.user)
    except MailboxAccount.DoesNotExist:
        raise Http404 from None
    messages.success(request, f"已断开 {account.email_address}，本地授权凭据已清除，邮件缓存保留。")
    return redirect("dashboard")


@login_required
@require_GET
@never_cache
def message_list(request, account_id):
    account = get_object_or_404(MailboxAccount, pk=account_id, owner=request.user)
    form = MailSearchForm(request.GET or None)
    # 空参数表示首次加载最近邮件，仍按默认页大小发起真实请求。
    if not form.is_bound:
        form = MailSearchForm({"query": "", "page_size": 10})
    emails, next_cursor = [], ""
    if form.is_valid():
        try:
            emails, next_cursor = search_messages(account.id, request.user, **form.cleaned_data)
        except MailError as exc:
            messages.error(request, str(exc))
    account.refresh_from_db()
    return render(
        request,
        "email_agent/message_list.html",
        {
            "account": account,
            "form": form,
            "emails": emails,
            "next_cursor": next_cursor,
            "query": form.cleaned_data.get("query", ""),
            "page_size": form.cleaned_data.get("page_size", 10),
        },
    )


@login_required
@require_GET
@never_cache
def message_detail(request, account_id, message_id):
    account = get_object_or_404(MailboxAccount, pk=account_id, owner=request.user)
    email = get_object_or_404(EmailMessage, pk=message_id, mailbox=account)
    cached = False
    try:
        email = read_message(account.id, request.user, email.id)
    except MailError as exc:
        messages.error(request, str(exc))
        cached = True
    return render(
        request,
        "email_agent/message_detail.html",
        {"account": account, "email": email, "cached": cached},
    )


@login_required
@require_GET
@never_cache
def conversation(request, account_id, message_id):
    account = get_object_or_404(MailboxAccount, pk=account_id, owner=request.user)
    source = get_object_or_404(EmailMessage, pk=message_id, mailbox=account)
    form = MailSearchForm(
        {
            "query": "",
            "page_size": request.GET.get("page_size", 10),
            "cursor": request.GET.get("cursor", ""),
        }
    )
    if not form.is_valid():
        return HttpResponseBadRequest("无效的会话分页参数。")
    emails, next_cursor = [], ""
    try:
        emails, next_cursor = read_thread(
            account.id,
            request.user,
            source.id,
            page_size=form.cleaned_data["page_size"],
            cursor=form.cleaned_data["cursor"],
        )
    except MailError as exc:
        messages.error(request, str(exc))
    return render(
        request,
        "email_agent/conversation.html",
        {
            "account": account,
            "source": source,
            "emails": emails,
            "next_cursor": next_cursor,
            "page_size": form.cleaned_data["page_size"],
        },
    )

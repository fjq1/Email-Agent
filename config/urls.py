"""项目路由：登录、邮箱、分析结果、候选事项及基础设施诊断。"""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from email_agent import analysis_views, mail_views, views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", views.dashboard, name="dashboard"),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("health/live/", views.liveness, name="liveness"),
    path("health/ready/", views.readiness, name="readiness"),
    path("probes/", views.start_probe, name="start_probe"),
    path("probes/<uuid:run_id>/review/", views.review_probe, name="review_probe"),
    path("probes/<uuid:run_id>/resume/", views.resume_run, name="resume_run"),
    path("mailboxes/setup/", mail_views.setup, name="mailbox_setup"),
    path("analyses/<uuid:analysis_id>/", analysis_views.detail, name="mail_analysis"),
    path("analyses/<uuid:analysis_id>/retry/", analysis_views.retry, name="mail_analysis_retry"),
    path("candidates/", analysis_views.candidate_list, name="task_candidates"),
    path("candidates/<uuid:candidate_id>/", analysis_views.candidate_detail, name="task_candidate"),
    path("mailboxes/oauth/<str:provider>/connect/", mail_views.connect, name="mailbox_connect"),
    path("mailboxes/oauth/<str:provider>/callback/", mail_views.callback, name="mailbox_callback"),
    path(
        "mailboxes/<uuid:account_id>/disconnect/", mail_views.disconnect, name="mailbox_disconnect"
    ),
    path("mailboxes/<uuid:account_id>/messages/", mail_views.message_list, name="mailbox_messages"),
    path(
        "mailboxes/<uuid:account_id>/messages/<uuid:message_id>/",
        mail_views.message_detail,
        name="mailbox_message",
    ),
    path(
        "mailboxes/<uuid:account_id>/messages/<uuid:message_id>/analyze/",
        analysis_views.start,
        name="mailbox_analyze",
    ),
    path(
        "mailboxes/<uuid:account_id>/messages/<uuid:message_id>/thread/",
        mail_views.conversation,
        name="mailbox_thread",
    ),
]

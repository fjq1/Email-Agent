"""项目路由：登录、邮箱、分析结果、候选事项及基础设施诊断。"""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from email_agent import analysis_views, command_views, mail_views, review_views, views

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
    path("commands/", command_views.index, name="commands"),
    path("commands/<uuid:plan_id>/", command_views.detail, name="command_detail"),
    path("commands/<uuid:plan_id>/edit/", command_views.edit, name="command_edit"),
    path("commands/<uuid:plan_id>/<str:operation>/", command_views.act, name="command_act"),
    path("preferences/", command_views.preferences, name="preferences"),
    path("preferences/<uuid:preference_id>/", command_views.preferences, name="preference_edit"),
    path(
        "preferences/<uuid:preference_id>/action/",
        command_views.preference_action,
        name="preference_action",
    ),
    path("analyses/<uuid:analysis_id>/", analysis_views.detail, name="mail_analysis"),
    path("analyses/<uuid:analysis_id>/retry/", analysis_views.retry, name="mail_analysis_retry"),
    path("candidates/", analysis_views.candidate_list, name="task_candidates"),
    path("candidates/<uuid:candidate_id>/", analysis_views.candidate_detail, name="task_candidate"),
    path("analyses/<uuid:analysis_id>/review/", review_views.prepare, name="prepare_review"),
    path("reviews/", review_views.review_list, name="action_reviews"),
    path("reviews/<uuid:proposal_id>/", review_views.detail, name="action_review"),
    path("reviews/<uuid:proposal_id>/edit/", review_views.edit, name="action_review_edit"),
    path("reviews/<uuid:proposal_id>/decide/", review_views.decide, name="action_review_decide"),
    path("reviews/<uuid:proposal_id>/resume/", review_views.resume, name="action_review_resume"),
    path("reviews/<uuid:proposal_id>/restart/", review_views.restart, name="action_review_restart"),
    path("reviews/<uuid:proposal_id>/revise/", review_views.revise, name="action_review_revise"),
    path("tasks/", review_views.task_list, name="tasks"),
    path("tasks/<uuid:task_id>/", review_views.task_detail, name="task_detail"),
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

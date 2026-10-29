from django.contrib import admin

from email_agent.models import (
    ActionAttempt,
    ActionExecution,
    ActionProposal,
    AgentRun,
    AnalysisResult,
    ApprovalDecision,
    AuditEvent,
    EmailMessage,
    MailboxAccount,
    OperationRecord,
    TaskCandidate,
    TaskItem,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    """运维后台只读展示，防止绕过业务入口修改审核结果。"""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AgentRun)
class AgentRunAdmin(ReadOnlyAdmin):
    list_display = ("id", "owner", "kind", "status", "created_at")
    list_filter = ("status", "kind")
    search_fields = ("id", "owner__username")


@admin.register(OperationRecord)
class OperationRecordAdmin(ReadOnlyAdmin):
    list_display = ("id", "run", "operation_type", "status", "created_at")


@admin.register(AuditEvent)
class AuditEventAdmin(ReadOnlyAdmin):
    list_display = ("event_type", "run", "created_at")


@admin.register(MailboxAccount)
class MailboxAccountAdmin(ReadOnlyAdmin):
    list_display = ("email_address", "provider", "owner", "status", "last_checked_at")
    list_filter = ("provider", "status")
    exclude = ("encrypted_credentials",)


@admin.register(EmailMessage)
class EmailMessageAdmin(ReadOnlyAdmin):
    list_display = ("subject", "mailbox", "received_at", "body_loaded")
    search_fields = ("subject", "mailbox__email_address")


@admin.register(AnalysisResult)
class AnalysisResultAdmin(ReadOnlyAdmin):
    list_display = ("id", "message", "provider", "model_name", "created_at")


@admin.register(TaskCandidate)
class TaskCandidateAdmin(ReadOnlyAdmin):
    list_display = ("title", "mailbox", "created_at")


@admin.register(ActionProposal)
class ActionProposalAdmin(ReadOnlyAdmin):
    list_display = ("id", "kind", "version", "review_state", "updated_at")


@admin.register(ApprovalDecision)
class ApprovalDecisionAdmin(ReadOnlyAdmin):
    list_display = ("proposal", "version", "decision", "actor", "created_at")


@admin.register(ActionExecution)
class ActionExecutionAdmin(ReadOnlyAdmin):
    list_display = ("id", "proposal", "version", "status", "updated_at")


@admin.register(ActionAttempt)
class ActionAttemptAdmin(ReadOnlyAdmin):
    list_display = ("execution", "number", "status", "started_at")


@admin.register(TaskItem)
class TaskItemAdmin(ReadOnlyAdmin):
    list_display = ("title", "owner", "status", "due_date", "created_at")

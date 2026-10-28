from django.contrib import admin

from email_agent.models import AgentRun, AuditEvent, OperationRecord, OutboxEvent


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


@admin.register(OutboxEvent)
class OutboxEventAdmin(ReadOnlyAdmin):
    list_display = ("id", "run", "attempts", "published_at", "last_error_code")

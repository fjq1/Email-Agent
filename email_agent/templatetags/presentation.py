"""仅负责展示名称和状态配色，业务状态及历史审批记录保持原样。"""

from django import template

register = template.Library()


@register.filter
def review_label(value):
    return {
        "pending": "待审核",
        "approved": "已确认",
        "rejected": "未执行",
        "cancelled": "未执行",
    }.get(value, value)


@register.filter
def status_tone(value):
    if value in {"succeeded", "completed", "connected", "done", "accepted"}:
        return "success"
    if value in {"failed", "reconnect_required"}:
        return "danger"
    if value in {"pending", "waiting_review", "preview", "ready", "unknown", "partial", "blocked"}:
        return "warning"
    if value in {"approved", "running", "processing", "dispatching"}:
        return "info"
    return "neutral"

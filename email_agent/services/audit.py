"""审计事件只记录必要信息，并对已知敏感字段脱敏。"""

from django.conf import settings

from email_agent.models import AuditEvent

SENSITIVE_NAMES = ("password", "secret", "token", "api_key", "authorization")


def redact(value):
    if isinstance(value, dict):
        return {
            key: "[已隐藏]"
            if any(name in str(key).lower() for name in SENSITIVE_NAMES)
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        secrets = [settings.SECRET_KEY, settings.DATABASES["default"].get("PASSWORD", "")]
        secrets += [config["api_key"] for config in settings.MODEL_PROVIDERS.values()]
        for secret in secrets:
            if secret and len(secret) >= 8:
                value = value.replace(secret, "[已隐藏]")
    return value


def record_event(run, event_type, event_key, **metadata):
    """稳定事件键避免节点重放产生重复审计记录。"""
    return AuditEvent.objects.get_or_create(
        event_key=event_key,
        defaults={"run": run, "event_type": event_type, "metadata": redact(metadata)},
    )[0]

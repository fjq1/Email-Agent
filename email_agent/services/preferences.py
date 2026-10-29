"""只保存用户主动确认的有限偏好，偏好不能授予发送权限或绕过人工审核。"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from email_agent.intelligence.models import PROVIDER_LABELS, provider_configured
from email_agent.models import MailboxAccount, UserPreference

LANGUAGES = {"auto": "跟随来信语言", "zh": "中文", "en": "英文"}
TONES = {"professional": "正式、专业", "friendly": "友好", "concise": "简洁"}


def display_preference(key, value):
    return {
        "label": dict(UserPreference.Key.choices).get(key, key),
        "display_value": LANGUAGES.get(value, value)
        if key == "reply_language"
        else TONES.get(value, value)
        if key == "reply_tone"
        else PROVIDER_LABELS.get(value, value)
        if key == "model_provider"
        else value,
    }


def validate_preference(key, value):
    value = str(value).strip()
    if key not in UserPreference.Key.values or not value or len(value) > 500:
        raise ValidationError("偏好类型或内容无效。")
    if key == "timezone_name":
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValidationError("请输入有效的 IANA 时区，例如 Asia/Shanghai。") from None
    if key == "reply_language" and value not in LANGUAGES:
        raise ValidationError("回复语言请选择 auto、zh 或 en。")
    if key == "reply_tone" and value not in TONES:
        raise ValidationError("回复风格请选择 professional、friendly 或 concise。")
    if key == "model_provider" and (value not in PROVIDER_LABELS or not provider_configured(value)):
        raise ValidationError(
            "该模型平台尚未配置完整，请在 .env 中填写 API Key 和模型名称后重启服务。"
        )
    return value


def effective_preferences(owner, mailbox=None):
    result = {}
    # 先读全局，再用已启用的邮箱级配置覆盖；禁用邮箱配置时会回退到全局。
    for scope in [None, mailbox] if mailbox else [None]:
        if scope is not None and scope.owner_id != owner.pk:
            raise MailboxAccount.DoesNotExist
        # 平台不是邮件风格偏好：它单独冻结在任务上，不交给模型推断或改写。
        for item in UserPreference.objects.filter(owner=owner, mailbox=scope, enabled=True).exclude(
            key=UserPreference.Key.MODEL_PROVIDER
        ):
            result[item.key] = {
                "id": str(item.id),
                "version": item.version,
                "value": item.value,
                "scope": "mailbox" if scope else "global",
            }
    return result


def describe_preferences(snapshot):
    return [
        {
            **display_preference(key, item["value"]),
            "version": item["version"],
            "scope": item["scope"],
        }
        for key, item in snapshot.items()
    ]


def save_preference(owner, *, key, value, mailbox=None, expected_version=0, source="settings"):
    value = validate_preference(key, value)
    if key == UserPreference.Key.MODEL_PROVIDER and mailbox is not None:
        raise ValidationError("模型平台适用于当前用户的所有邮箱，请选择全局范围。")
    if mailbox and mailbox.owner_id != owner.pk:
        raise MailboxAccount.DoesNotExist
    with transaction.atomic():
        # 用户行作为全局/邮箱偏好的共同锁，也保护“当前还没有偏好行”的首次保存。
        get_user_model().objects.select_for_update().get(pk=owner.pk)
        item = (
            UserPreference.objects.select_for_update()
            .filter(owner=owner, mailbox=mailbox, key=key)
            .first()
        )
        if (
            item
            and item.confirmation_source == source
            and source.startswith("command:")
            and item.value == value
            and item.enabled
        ):
            return item
        if str(item.version if item else 0) != str(expected_version):
            raise ValidationError("这项偏好已在其他页面更新，请刷新后重新确认。")
        if item is None:
            return UserPreference.objects.create(
                owner=owner,
                mailbox=mailbox,
                key=key,
                value=value,
                confirmation_source=source,
                confirmed_at=timezone.now(),
            )
        item.value, item.enabled = value, True
        item.version += 1
        item.confirmed_at, item.confirmation_source = timezone.now(), source
        item.save()
        return item


def change_preference(preference_id, owner, version, operation):
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=owner.pk)
        item = UserPreference.objects.select_for_update().get(pk=preference_id, owner=owner)
        if str(item.version) != str(version):
            raise ValidationError("偏好版本已变化，请刷新页面。")
        if operation == "delete":
            item.delete()
        elif operation == "toggle":
            item.enabled = not item.enabled
            item.version += 1
            item.save(update_fields=["enabled", "version", "updated_at"])
        else:
            raise ValidationError("无效的偏好操作。")

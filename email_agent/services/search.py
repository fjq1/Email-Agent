"""将受约束的搜索条件转成服务商原生语法，并进行精确时区/已读状态复核。"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError

from email_agent.intelligence.commands import SearchFilters


def date_bounds(filters, timezone_name):
    zone = ZoneInfo(timezone_name)
    lower = datetime.combine(filters.date_from, time.min, zone) if filters.date_from else None
    upper = (
        datetime.combine(filters.date_to + timedelta(days=1), time.min, zone)
        if filters.date_to
        else None
    )
    return lower, upper


def build_query(provider, values, timezone_name):
    filters = SearchFilters.model_validate(values)
    terms = []
    for key, value in (
        ("from", filters.sender),
        ("to", filters.recipient),
        ("subject", filters.subject),
    ):
        if value:
            terms.append(f'{key}:"{value}"')
    terms.extend(f'"{value}"' for value in filters.keywords if value)
    lower, upper = date_bounds(filters, timezone_name)
    if provider == "gmail":
        # Gmail 的 YYYY/MM/DD 按固定时区解释；时间戳可保持用户所选时区的边界。
        if lower:
            terms.append(f"after:{int(lower.timestamp()) - 1}")
        if upper:
            terms.append(f"before:{int(upper.timestamp())}")
        if filters.unread is not None:
            terms.append("is:unread" if filters.unread else "is:read")
        query = " ".join(terms)
    else:
        # Graph KQL 日期先放宽一天召回，再用实际 receivedDateTime 按精确时区过滤。
        if lower:
            day = lower.astimezone(UTC).date() - timedelta(days=1)
            terms.append(f"received>={day.isoformat()}")
        if upper:
            day = upper.astimezone(UTC).date() + timedelta(days=1)
            terms.append(f"received<{day.isoformat()}")
        query = " AND ".join(terms)
    if len(query) > 500:
        raise ValidationError("检索条件过长，请减少关键词。")
    return query


def matches_metadata(message, values, timezone_name):
    filters = SearchFilters.model_validate(values)
    lower, upper = date_bounds(filters, timezone_name)
    if lower or upper:
        if (
            message.received_at is None
            or (lower and message.received_at < lower)
            or (upper and message.received_at >= upper)
        ):
            return False
    if filters.unread is not None and message.is_read is not (not filters.unread):
        return False
    return True

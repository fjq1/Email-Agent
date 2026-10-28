"""至少一次投递配合业务幂等，抵御队列重复消息。"""

import logging
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from email_agent.models import OutboxEvent

logger = logging.getLogger(__name__)


def dispatch_pending(limit=100):
    from email_agent.tasks import advance_probe

    published = 0
    for _ in range(limit):
        now = timezone.now()
        with transaction.atomic():
            event = (
                OutboxEvent.objects.select_for_update(skip_locked=True)
                .filter(
                    published_at__isnull=True,
                    available_at__lte=now,
                )
                .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
                .first()
            )
            if event is None:
                break
            event.claim_token = uuid.uuid4()
            event.lease_until = now + timedelta(seconds=60)
            event.attempts += 1
            event.save(update_fields=["claim_token", "lease_until", "attempts", "updated_at"])
        # 网络操作放在事务外，避免队列故障长时间占用行锁。
        claimed = OutboxEvent.objects.filter(pk=event.pk, claim_token=event.claim_token)
        try:
            if event.event_type != "advance_probe":
                raise ValueError("未知的后台事件类型")
            advance_probe.apply_async(args=[str(event.run_id)], retry=False)
        except Exception as exc:
            claimed.update(
                claim_token=None,
                lease_until=None,
                available_at=timezone.now()
                + timedelta(seconds=min(2 ** min(event.attempts, 8), 300)),
                last_error_code=type(exc).__name__,
            )
            logger.warning("事件投递失败，稍后重试：%s", type(exc).__name__)
        else:
            claimed.update(
                published_at=timezone.now(), claim_token=None, lease_until=None, last_error_code=""
            )
            published += 1
    return published

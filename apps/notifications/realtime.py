import asyncio
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings


logger = logging.getLogger(__name__)


async def _send_with_timeout(channel_layer, group: str, message: dict) -> None:
    timeout = float(getattr(settings, "MASTERGO_REALTIME_SEND_TIMEOUT_SECONDS", 2.0))
    await asyncio.wait_for(channel_layer.group_send(group, message), timeout=timeout)


def safe_group_send(group: str, message: dict) -> bool:
    """Best-effort realtime delivery with a strict latency bound.

    PostgreSQL remains the source of truth and both mobile apps perform HTTP
    catch-up, so a Redis outage must not hold an API request open or roll back a
    business transition.
    """
    try:
        async_to_sync(_send_with_timeout)(get_channel_layer(), group, message)
        return True
    except TimeoutError:
        logger.warning("Realtime delivery timed out for group %s", group)
        return False
    except Exception:
        logger.warning("Realtime delivery failed for group %s", group, exc_info=True)
        return False

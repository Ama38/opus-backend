import logging
import threading

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone


logger = logging.getLogger(__name__)
_started = False
_start_lock = threading.Lock()
_stop_event = threading.Event()

_LEADERBOARD_CACHE_KEY = "leaderboard:last_snapshot_at"


def _maybe_snapshot_leaderboard() -> None:
    """Recompute the leaderboard on an interval, gated via cache.

    Without this, leaderboard_rank/rank_change only ever get set by manually
    running `manage.py snapshot_leaderboard`, which nothing schedules unless
    someone configures an external cron on top of the deploy. Running it
    here means the leaderboard works out of the box.
    """
    from django.core.cache import cache

    from apps.masters.services import snapshot_leaderboard

    interval_hours = max(
        1.0,
        float(getattr(settings, "MASTERGO_LEADERBOARD_SNAPSHOT_INTERVAL_HOURS", 168)),
    )
    now = timezone.now()
    last_run = cache.get(_LEADERBOARD_CACHE_KEY)
    if last_run is not None and (now - last_run).total_seconds() < interval_hours * 3600:
        return
    snapshot_leaderboard()
    cache.set(_LEADERBOARD_CACHE_KEY, now, timeout=None)


def _run() -> None:
    from .tasks import sweep_offer_expirations

    interval = max(
        1.0,
        float(getattr(settings, "MASTERGO_ORDER_SWEEPER_INTERVAL_SECONDS", 5)),
    )
    while not _stop_event.is_set():
        try:
            close_old_connections()
            sweep_offer_expirations(limit=20)
            _maybe_snapshot_leaderboard()
        except Exception:
            logger.exception("Order sweeper iteration failed.")
        finally:
            close_old_connections()
        _stop_event.wait(interval)


def start_order_sweeper() -> None:
    """Start one bounded worker instead of one Timer thread per offer."""
    global _started
    if _started:
        return
    with _start_lock:
        if _started:
            return
        threading.Thread(
            target=_run,
            name="mastergo-order-sweeper",
            daemon=True,
        ).start()
        _started = True

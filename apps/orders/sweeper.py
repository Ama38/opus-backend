import logging
import threading

from django.conf import settings
from django.db import close_old_connections


logger = logging.getLogger(__name__)
_started = False
_start_lock = threading.Lock()
_stop_event = threading.Event()


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

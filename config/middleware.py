import logging
import time
import uuid

from django.conf import settings


logger = logging.getLogger("mastergo.requests")


class RequestTimingMiddleware:
    """Expose request latency and log slow endpoints in Railway logs."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.monotonic()
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
        try:
            response = self.get_response(request)
        finally:
            elapsed = time.monotonic() - started
            threshold = float(
                getattr(settings, "MASTERGO_SLOW_REQUEST_SECONDS", 1.0)
            )
            if elapsed >= threshold:
                logger.warning(
                    "slow_request request_id=%s method=%s path=%s elapsed_ms=%d",
                    request_id,
                    request.method,
                    request.path,
                    round(elapsed * 1000),
                )
        response["X-Request-Id"] = request_id
        response["Server-Timing"] = f"app;dur={elapsed * 1000:.1f}"
        return response

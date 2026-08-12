"""Firebase Cloud Messaging (HTTP v1) delivery.

Credentials are loaded from, in order of preference:
  * FIREBASE_CREDENTIALS_JSON  - the service-account JSON as a raw string (best
    for Railway / env-only deploys);
  * FIREBASE_CREDENTIALS_FILE  - a filesystem path to the service-account JSON
    (best for local dev, keep the file out of git);
  * GOOGLE_APPLICATION_CREDENTIALS - standard Google SDK path variable.

If none are present the module degrades gracefully: push sends become no-ops and
the rest of the app (in-app notifications, WebSocket) keeps working. This keeps
local dev and tests running without Firebase configured.
"""

from __future__ import annotations

import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

_init_lock = threading.Lock()
_app = None
_init_attempted = False


def _load_credentials():
    from firebase_admin import credentials

    raw_json = os.getenv("FIREBASE_CREDENTIALS_JSON")
    if raw_json:
        return credentials.Certificate(json.loads(raw_json))

    path = os.getenv("FIREBASE_CREDENTIALS_FILE") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if path and os.path.exists(path):
        return credentials.Certificate(path)

    return None


def _get_app():
    """Return the initialised firebase app, or None if not configured."""
    global _app, _init_attempted

    if _app is not None:
        return _app
    if _init_attempted:
        return None

    with _init_lock:
        if _app is not None:
            return _app
        if _init_attempted:
            return None
        _init_attempted = True
        try:
            import firebase_admin

            cred = _load_credentials()
            if cred is None:
                logger.warning("FCM disabled: no Firebase credentials configured.")
                return None
            _app = firebase_admin.initialize_app(cred)
            logger.info("FCM initialised.")
            return _app
        except Exception:  # pragma: no cover - defensive, keeps app booting
            logger.exception("FCM initialisation failed; push disabled.")
            return None


def is_configured() -> bool:
    return _get_app() is not None


def send_push_to_user(
    user,
    *,
    title: str,
    body: str,
    data: dict | None = None,
    channel_id: str = "incoming_orders",
    sound: str = "incoming_call",
    include_notification: bool = False,
) -> int:
    """Send a high-priority push to every active device token of ``user``.

    Returns the number of messages accepted by FCM. Invalid/expired tokens are
    deactivated so they are not retried. No-ops (returns 0) when FCM is not
    configured.
    """
    app = _get_app()
    if app is None:
        return 0

    from firebase_admin import messaging

    from .models import DeviceToken

    tokens = list(
        DeviceToken.objects.filter(user=user, is_active=True).values_list("token", flat=True)
    )
    if not tokens:
        return 0

    # Keep display fields in data as well so the app can build a richer local
    # notification while it is running and route a tap to the relevant order.
    string_data = {str(k): str(v) for k, v in (data or {}).items()}
    string_data.update(
        {
            "title": title,
            "body": body,
            "channel_id": channel_id,
            "sound": sound,
        }
    )

    # A notification block lets Android display the push itself while the app
    # process is backgrounded or terminated. Data-only delivery is best-effort
    # in those states and is not sufficient for time-sensitive order offers.
    notification = (
        messaging.Notification(title=title, body=body) if include_notification else None
    )
    android_notification = (
        messaging.AndroidNotification(channel_id=channel_id, sound=sound)
        if include_notification
        else None
    )
    android = messaging.AndroidConfig(
        priority="high",
        notification=android_notification,
    )

    sent = 0
    invalid_tokens: list[str] = []
    for token in tokens:
        message = messaging.Message(
            token=token,
            data=string_data,
            android=android,
            notification=notification,
        )
        try:
            messaging.send(message)
            sent += 1
        except messaging.UnregisteredError:
            invalid_tokens.append(token)
        except Exception:  # pragma: no cover - network/quled errors shouldn't break flow
            logger.exception("FCM send failed for a token; continuing.")

    if invalid_tokens:
        DeviceToken.objects.filter(token__in=invalid_tokens).update(is_active=False)

    return sent

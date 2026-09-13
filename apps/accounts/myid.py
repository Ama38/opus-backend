"""MyID.uz government face-scan identification integration.

MyID's mobile SDK only performs the on-device face/passport capture; it never
hands personal data (name, birth date, PINFL, ...) to the app. The verified
profile is fetched by this backend, server-to-server, using `client_secret`
(which must never reach the mobile app) after the SDK reports a one-time
`code`. Docs: https://docs.myid.uz/#/ru/sdknew
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("mastergo.myid")

_TOKEN_CACHE_KEY = "myid:access_token"
_HTTP_TIMEOUT_SECONDS = 15


class MyIdError(Exception):
    """Raised when a MyID API call fails."""


def _host() -> str:
    return getattr(settings, "MYID_HOST", "https://api.devmyid.uz").rstrip("/")


def _fetch_access_token() -> tuple[str, int]:
    client_id = getattr(settings, "MYID_CLIENT_ID", "")
    client_secret = getattr(settings, "MYID_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise MyIdError("myid_not_configured")
    resp = requests.post(
        f"{_host()}/api/v1/auth/clients/access-token",
        json={"client_id": client_id, "client_secret": client_secret},
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        raise MyIdError(f"myid_auth_failed:{resp.status_code}:{resp.text[:200]}")
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise MyIdError("myid_auth_no_token")
    return token, int(data.get("expires_in") or 3600)


def _access_token(*, force_refresh: bool = False) -> str:
    if not force_refresh:
        cached = cache.get(_TOKEN_CACHE_KEY)
        if cached:
            return cached
    token, expires_in = _fetch_access_token()
    # Refresh a minute before actual expiry so a cached token is never used
    # right up against the edge.
    cache.set(_TOKEN_CACHE_KEY, token, timeout=max(expires_in - 60, 60))
    return token


def _authorized_request(method: str, path: str, **kwargs: Any) -> requests.Response:
    # One retry with a fresh token if the cached one was rejected.
    for attempt in range(2):
        token = _access_token(force_refresh=attempt == 1)
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        resp = requests.request(
            method,
            f"{_host()}{path}",
            headers=headers,
            timeout=_HTTP_TIMEOUT_SECONDS,
            **kwargs,
        )
        if resp.status_code == 401 and attempt == 0:
            continue
        return resp
    raise MyIdError("myid_unauthorized")


def create_session(*, phone_number: str | None = None) -> str:
    """Create an empty MyID identification session and return its session_id.

    Created empty (no passport data pre-filled) so the SDK shows its own
    passport-entry screen before the face capture -- the master types their
    document series/number once, on-device.
    """
    payload: dict[str, Any] = {}
    if phone_number:
        payload["phone_number"] = phone_number
    resp = _authorized_request("POST", "/api/v2/sdk/sessions", json=payload)
    if resp.status_code != 200:
        raise MyIdError(f"myid_session_failed:{resp.status_code}:{resp.text[:200]}")
    session_id = resp.json().get("session_id")
    if not session_id:
        raise MyIdError("myid_session_no_id")
    return session_id


def get_identification_data(code: str) -> dict[str, Any]:
    """Exchange the SDK's one-time `code` for the verified user profile."""
    resp = _authorized_request("GET", "/api/v1/sdk/data", params={"code": code})
    if resp.status_code != 200:
        raise MyIdError(f"myid_data_failed:{resp.status_code}:{resp.text[:200]}")
    return resp.json()

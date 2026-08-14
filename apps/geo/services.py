import json
import time
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache

from apps.notifications.realtime import safe_group_send

from .models import MasterLocationPing

MAPBOX_FORWARD_GEOCODE_URL = "https://api.mapbox.com/search/geocode/v6/forward"
MAPBOX_REVERSE_GEOCODE_URL = "https://api.mapbox.com/search/geocode/v6/reverse"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
TASHKENT_PROXIMITY = "69.2401,41.2995"
TASHKENT_VIEWBOX = "69.10,41.40,69.50,41.18"
REVERSE_GEOCODE_CACHE_SECONDS = 60 * 60 * 24
SEARCH_GEOCODE_CACHE_SECONDS = 60 * 60
NOMINATIM_LOCK_KEY = "geo:nominatim:request:lock"


def broadcast_master_location_ping(ping: MasterLocationPing) -> None:
    if ping.order_id is None:
        return

    safe_group_send(
        f"order_{ping.order_id}",
        {
            "type": "order.event",
            "payload": {
                "event": "order.master_location",
                "order_id": str(ping.order_id),
                "ping_id": ping.id,
                "master_id": ping.master_id,
                "latitude": str(ping.latitude),
                "longitude": str(ping.longitude),
                "accuracy_meters": ping.accuracy_meters,
                "heading_degrees": ping.heading_degrees,
                "speed_mps": str(ping.speed_mps) if ping.speed_mps is not None else None,
                "created_at": ping.created_at.isoformat(),
            },
        },
    )


def reverse_geocode(latitude: Decimal, longitude: Decimal) -> str:
    """Resolve with Mapbox; use rate-limited Nominatim only as fallback.

    Mapbox Geocoding v6 results are temporary by default, so they are not
    cached. Only fallback results use the application cache.
    """

    key = getattr(settings, "MAPBOX_ACCESS_TOKEN", "").strip()
    if key:
        result = _reverse_mapbox(latitude, longitude, key)
        if result:
            return result
    return _reverse_nominatim(latitude, longitude)


def search_address(query: str, limit: int = 6) -> list[dict]:
    """Autocomplete with Mapbox; use Nominatim only when unavailable."""

    cleaned = (query or "").strip()
    if len(cleaned) < 3:
        return []

    key = getattr(settings, "MAPBOX_ACCESS_TOKEN", "").strip()
    if key:
        results = _search_mapbox(cleaned, key, limit)
        if results:
            return results
    return _search_nominatim(cleaned, limit)


def _reverse_mapbox(latitude: Decimal, longitude: Decimal, key: str) -> str | None:
    params = urlencode(
        {
            "access_token": key,
            "longitude": f"{float(longitude):.6f}",
            "latitude": f"{float(latitude):.6f}",
            "language": "ru,uz,en",
        }
    )
    payload = _fetch_json(f"{MAPBOX_REVERSE_GEOCODE_URL}?{params}")
    if not payload:
        return None
    for feature in payload.get("features") or []:
        label = _mapbox_feature_label(feature)
        if label:
            return label
    return None


def _search_mapbox(query: str, key: str, limit: int) -> list[dict]:
    params = urlencode(
        {
            "access_token": key,
            "q": query,
            "autocomplete": "true",
            "country": "uz",
            "language": "ru,uz,en",
            "limit": max(1, min(limit, 10)),
            "proximity": TASHKENT_PROXIMITY,
        }
    )
    payload = _fetch_json(f"{MAPBOX_FORWARD_GEOCODE_URL}?{params}")
    if not payload:
        return []

    results: list[dict] = []
    for feature in payload.get("features") or []:
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        if len(coordinates) < 2:
            continue
        try:
            longitude = float(coordinates[0])
            latitude = float(coordinates[1])
        except (TypeError, ValueError):
            continue
        label = _mapbox_feature_label(feature)
        if not label:
            continue
        results.append(
            {
                "address_text": label,
                "latitude": latitude,
                "longitude": longitude,
            }
        )
    return results


def _mapbox_feature_label(feature: dict) -> str:
    properties = feature.get("properties") or {}
    full_address = properties.get("full_address")
    if full_address:
        return str(full_address)
    name = properties.get("name_preferred") or properties.get("name")
    context = properties.get("place_formatted")
    if name and context:
        return f"{name}, {context}"
    return str(name or feature.get("place_name") or "")


def _fetch_json(url: str) -> dict | None:
    request = Request(url, headers={"User-Agent": "MasterGo/1.0"})
    try:
        with urlopen(request, timeout=6) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _reverse_nominatim(latitude: Decimal, longitude: Decimal) -> str:
    rounded_latitude = round(float(latitude), 4)
    rounded_longitude = round(float(longitude), 4)
    cache_key = f"geo:nominatim:reverse:{rounded_latitude:.4f}:{rounded_longitude:.4f}"
    cached = cache.get(cache_key)
    if cached:
        return str(cached)

    _wait_for_nominatim_slot()
    query = urlencode(
        {
            "format": "jsonv2",
            "lat": f"{float(latitude):.6f}",
            "lon": f"{float(longitude):.6f}",
            "accept-language": "ru,uz,en",
        }
    )
    request = Request(
        f"{NOMINATIM_REVERSE_URL}?{query}",
        headers={"User-Agent": "MasterGo/1.0"},
    )
    try:
        with urlopen(request, timeout=6) as response:
            payload = json.loads(response.read().decode("utf-8"))
        address_text = payload.get("display_name")
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        address_text = None
    result = address_text or f"{float(latitude):.5f}, {float(longitude):.5f}"
    cache.set(cache_key, result, REVERSE_GEOCODE_CACHE_SECONDS)
    return result


def _search_nominatim(query: str, limit: int) -> list[dict]:
    cache_key = f"geo:nominatim:search:{query.lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return list(cached)

    _wait_for_nominatim_slot()
    params = urlencode(
        {
            "format": "jsonv2",
            "q": query,
            "limit": limit,
            "addressdetails": 1,
            "accept-language": "ru,uz,en",
            "countrycodes": "uz",
            "viewbox": TASHKENT_VIEWBOX,
            "bounded": 0,
        }
    )
    request = Request(
        f"{NOMINATIM_SEARCH_URL}?{params}",
        headers={"User-Agent": "MasterGo/1.0"},
    )
    try:
        with urlopen(request, timeout=6) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []

    results = []
    for item in payload:
        try:
            latitude = float(item["lat"])
            longitude = float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        results.append(
            {
                "address_text": item.get("display_name", ""),
                "latitude": latitude,
                "longitude": longitude,
            }
        )
    cache.set(cache_key, results, SEARCH_GEOCODE_CACHE_SECONDS)
    return results


def _wait_for_nominatim_slot() -> None:
    for _ in range(12):
        if cache.add(NOMINATIM_LOCK_KEY, "1", timeout=1):
            return
        time.sleep(0.1)
    cache.add(NOMINATIM_LOCK_KEY, "1", timeout=1)

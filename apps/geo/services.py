import json
import time
from decimal import Decimal
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache

from apps.notifications.realtime import safe_group_send

from .models import MasterLocationPing

NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
TWOGIS_GEOCODE_URL = "https://catalog.api.2gis.com/3.0/items/geocode"
# Bias geocoding toward Tashkent (lon,lat).
TASHKENT_LOCATION = "69.2401,41.2995"
REVERSE_GEOCODE_CACHE_SECONDS = 60 * 60 * 24
SEARCH_GEOCODE_CACHE_SECONDS = 60 * 60
NOMINATIM_LOCK_KEY = "geo:nominatim:reverse:lock"
# Tashkent-biased viewbox so address search prioritises the city.
TASHKENT_VIEWBOX = "69.10,41.40,69.50,41.18"


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
    rounded_latitude = round(float(latitude), 4)
    rounded_longitude = round(float(longitude), 4)
    cache_key = f"geo:reverse:{rounded_latitude:.4f}:{rounded_longitude:.4f}"
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
    with urlopen(request, timeout=6) as response:
        payload = json.loads(response.read().decode("utf-8"))
    address_text = payload.get("display_name") or f"{float(latitude):.5f}, {float(longitude):.5f}"
    cache.set(cache_key, address_text, REVERSE_GEOCODE_CACHE_SECONDS)
    return address_text


def search_address(query: str, limit: int = 6) -> list[dict]:
    cleaned = (query or "").strip()
    if len(cleaned) < 3:
        return []

    cache_key = f"geo:search:{cleaned.lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return list(cached)

    results: list[dict] = []
    key = getattr(settings, "MASTERGO_2GIS_KEY", "")
    if key:
        results = _search_2gis(cleaned, key, limit)
    if not results:
        results = _search_nominatim(cleaned, limit)

    cache.set(cache_key, results, SEARCH_GEOCODE_CACHE_SECONDS)
    return results


def _search_2gis(query: str, key: str, limit: int) -> list[dict]:
    params = urlencode(
        {
            "key": key,
            "q": query,
            "fields": "items.point,items.full_name,items.address_name",
            "location": TASHKENT_LOCATION,
            "page_size": limit,
        }
    )
    request = Request(
        f"{TWOGIS_GEOCODE_URL}?{params}",
        headers={"User-Agent": "MasterGo/1.0"},
    )
    try:
        with urlopen(request, timeout=6) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []

    items = (payload.get("result") or {}).get("items") or []
    results = []
    for item in items:
        point = item.get("point") or {}
        latitude = point.get("lat")
        longitude = point.get("lon")
        if latitude is None or longitude is None:
            continue
        label = item.get("full_name") or item.get("address_name") or item.get("name") or ""
        if not label:
            continue
        results.append(
            {
                "address_text": label,
                "latitude": float(latitude),
                "longitude": float(longitude),
            }
        )
    return results


def _search_nominatim(query: str, limit: int) -> list[dict]:
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
    except Exception:
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
    return results


def _wait_for_nominatim_slot() -> None:
    for _ in range(12):
        if cache.add(NOMINATIM_LOCK_KEY, "1", timeout=1):
            return
        time.sleep(0.1)
    cache.add(NOMINATIM_LOCK_KEY, "1", timeout=1)

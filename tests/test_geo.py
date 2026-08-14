import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User


class ReverseGeocodeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.user = User.objects.create_user(phone="+998901234567", full_name="Geo User")
        self.api.force_authenticate(user=self.user)

    @override_settings(MAPBOX_ACCESS_TOKEN="")
    def test_nominatim_fallback_caches_rounded_coordinates(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps({"display_name": "Tashkent, Chilanzar"}).encode("utf-8")

        with patch("apps.geo.services.urlopen", return_value=FakeResponse()) as mocked:
            first = self.api.get("/api/geo/reverse/", {"lat": "41.31001", "lng": "69.27001"})
            second = self.api.get("/api/geo/reverse/", {"lat": "41.31002", "lng": "69.27002"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), {"address_text": "Tashkent, Chilanzar"})
        self.assertEqual(second.json(), {"address_text": "Tashkent, Chilanzar"})
        mocked.assert_called_once()
        request = mocked.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), "MasterGo/1.0")

    @override_settings(MAPBOX_ACCESS_TOKEN="pk.test-mapbox-token")
    def test_reverse_geocode_uses_mapbox_without_caching_temporary_results(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps(
                    {
                        "features": [
                            {
                                "properties": {
                                    "full_address": "Chilonzor, Toshkent, Uzbekistan"
                                }
                            }
                        ]
                    }
                ).encode("utf-8")

        with patch("apps.geo.services.urlopen", return_value=FakeResponse()) as mocked:
            first = self.api.get("/api/geo/reverse/", {"lat": "41.31001", "lng": "69.27001"})
            second = self.api.get("/api/geo/reverse/", {"lat": "41.31001", "lng": "69.27001"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), {"address_text": "Chilonzor, Toshkent, Uzbekistan"})
        self.assertEqual(mocked.call_count, 2)
        request = mocked.call_args.args[0]
        url = urlparse(request.full_url)
        params = parse_qs(url.query)
        self.assertEqual(url.path, "/search/geocode/v6/reverse")
        self.assertEqual(params["access_token"], ["pk.test-mapbox-token"])
        self.assertEqual(params["longitude"], ["69.270010"])
        self.assertEqual(params["latitude"], ["41.310010"])

    @override_settings(MAPBOX_ACCESS_TOKEN="pk.test-mapbox-token")
    def test_address_search_uses_mapbox_and_preserves_coordinate_order(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps(
                    {
                        "features": [
                            {
                                "geometry": {"coordinates": [69.2401, 41.2995]},
                                "properties": {
                                    "name": "Amir Temur Square",
                                    "place_formatted": "Tashkent, Uzbekistan",
                                },
                            }
                        ]
                    }
                ).encode("utf-8")

        with patch("apps.geo.services.urlopen", return_value=FakeResponse()) as mocked:
            response = self.api.get("/api/geo/search/", {"q": "Amir Temur"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "results": [
                    {
                        "address_text": "Amir Temur Square, Tashkent, Uzbekistan",
                        "latitude": 41.2995,
                        "longitude": 69.2401,
                    }
                ]
            },
        )
        request = mocked.call_args.args[0]
        url = urlparse(request.full_url)
        params = parse_qs(url.query)
        self.assertEqual(url.path, "/search/geocode/v6/forward")
        self.assertEqual(params["country"], ["uz"])
        self.assertEqual(params["proximity"], ["69.2401,41.2995"])

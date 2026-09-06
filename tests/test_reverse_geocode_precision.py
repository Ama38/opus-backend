from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.geo.services import reverse_geocode


@override_settings(MAPBOX_ACCESS_TOKEN="pk.test")
class ReverseGeocodePrecisionTests(SimpleTestCase):
    def resolve(self, features):
        with patch("apps.geo.services._fetch_json", return_value={"features": features}), patch(
            "apps.geo.services._reverse_nominatim", return_value="Навои, 15, Ташкент"
        ) as fallback:
            result = reverse_geocode(Decimal("41.3"), Decimal("69.2"))
        return result, fallback

    def test_city_result_uses_fallback(self):
        result, fallback = self.resolve([
            {"properties": {"feature_type": "place", "full_address": "Ташкент, Узбекистан"}}
        ])
        self.assertEqual(result, "Навои, 15, Ташкент")
        fallback.assert_called_once()

    def test_detailed_result_does_not_use_fallback(self):
        for kind in ("address", "street"):
            with self.subTest(kind=kind):
                result, fallback = self.resolve([
                    {"properties": {"feature_type": kind, "full_address": "Навои, Ташкент"}}
                ])
                self.assertEqual(result, "Навои, Ташкент")
                fallback.assert_not_called()

    def test_missing_features_use_fallback(self):
        _, fallback = self.resolve([])
        fallback.assert_called_once()

    def test_coarse_feature_does_not_hide_later_street(self):
        result, fallback = self.resolve([
            {"properties": {"feature_type": "place", "name": "Ташкент"}},
            {"properties": {"feature_type": "street", "name": "Навои"}},
        ])
        self.assertEqual(result, "Навои")
        fallback.assert_not_called()

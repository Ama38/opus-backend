from decimal import Decimal
import json
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.orders.models import Order, OrderStatus
from apps.accounts.models import User
from apps.billing.models import MasterWallet
from apps.billing.services import activate_package, top_up_wallet
from apps.geo.models import MasterLocationPing
from apps.masters.models import MasterCategoryPrice, MasterProfile, MasterStatus, ServiceCategory


class APISmokeTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.category = ServiceCategory.objects.create(
            slug="electrician",
            name_ru="Электрик",
            name_uz="Elektrik",
            icon="⚡",
            sort_order=1,
        )

    def test_public_category_endpoint(self):
        response = self.api.get("/api/categories/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["slug"], "electrician")

    def test_geo_route_returns_fallback_geometry_without_osrm(self):
        user = User.objects.create_user(phone="+998901111111", full_name="Client")
        self.api.force_authenticate(user=user)

        response = self.api.get(
            "/api/geo/route/",
            {
                "from_latitude": "41.313000",
                "from_longitude": "69.242000",
                "to_latitude": "41.312000",
                "to_longitude": "69.241000",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["provider"], "fallback")
        self.assertTrue(body["is_fallback"])
        self.assertEqual(len(body["points"]), 2)
        self.assertGreater(body["eta_seconds"], 0)

    @override_settings(OSRM_ENABLED=True, OSRM_BASE_URL="https://osrm.test")
    def test_geo_route_uses_osrm_geometry_when_enabled(self):
        user = User.objects.create_user(phone="+998901111112", full_name="Client")
        self.api.force_authenticate(user=user)
        payload = {
            "routes": [
                {
                    "distance": 1234.5,
                    "duration": 321.4,
                    "geometry": {
                        "coordinates": [
                            [69.242000, 41.313000],
                            [69.242500, 41.312500],
                            [69.241000, 41.312000],
                        ]
                    },
                }
            ]
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        with patch("apps.geo.providers.urlopen", return_value=FakeResponse()) as mocked:
            response = self.api.get(
                "/api/geo/route/",
                {
                    "from_latitude": "41.313000",
                    "from_longitude": "69.242000",
                    "to_latitude": "41.312000",
                    "to_longitude": "69.241000",
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["provider"], "osrm")
        self.assertFalse(body["is_fallback"])
        self.assertEqual(body["distance_meters"], 1234)
        self.assertEqual(body["eta_seconds"], 321)
        self.assertEqual(len(body["points"]), 3)
        mocked.assert_called_once()

    def test_mock_otp_returns_token_and_token_can_create_order(self):
        with patch("apps.accounts.services.random.randint", return_value=1234):
            response = self.api.post("/api/auth/otp/start/", {"phone": "+998903333333"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["sent"])
        self.assertIn("expires_at", response.json())
        self.assertNotIn("mock_code", response.json())

        response = self.api.post(
            "/api/auth/otp/verify/",
            {"phone": "+998903333333", "code": "1234", "full_name": "Token Client"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        token = response.json()["token"]
        self.assertTrue(token)

        self.api.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        response = self.api.post(
            "/api/orders/",
            {
                "category_id": self.category.id,
                "description": "Need help today",
                "address_text": "Tashkent",
                "latitude": "41.312000",
                "longitude": "69.241000",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["client_phone"], "+998903333333")

    def test_new_master_can_open_and_top_up_wallet_without_existing_profile(self):
        master_user = User.objects.create_user(phone="+998909311391", full_name="New Master")
        self.api.force_authenticate(user=master_user)

        response = self.api.get("/api/wallets/me/")

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.json()["wallet"])

        response = self.api.post(
            "/api/wallets/top-up/",
            {"amount_uzs": 50_000, "note": "Regression top-up"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["wallet"]["balance_uzs"], 50_000)
        self.assertTrue(MasterProfile.objects.filter(user=master_user).exists())

    def test_client_can_create_order_and_master_can_accept_price_flow(self):
        client = User.objects.create_user(phone="+998901111111", full_name="Client")
        master_user = User.objects.create_user(phone="+998902222222", full_name="Master")
        master = MasterProfile.objects.create(
            user=master_user,
            status=MasterStatus.APPROVED,
            is_online=True,
            current_latitude=Decimal("41.311081"),
            current_longitude=Decimal("69.240562"),
            rating=Decimal("4.90"),
            activity_points=700,
        )
        MasterCategoryPrice.objects.create(
            master=master,
            category=self.category,
            min_price_uzs=80_000,
            max_price_uzs=250_000,
        )
        wallet = MasterWallet.objects.create(master=master)
        top_up_wallet(wallet, 40_001)
        activate_package(master, orders_count=10, days=30)

        self.api.force_authenticate(user=client)
        response = self.api.post(
            "/api/orders/",
            {
                "category_id": self.category.id,
                "description": "No light in room",
                "address_text": "Tashkent, Navoi 15",
                "latitude": "41.312000",
                "longitude": "69.241000",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        order_id = response.json()["id"]

        response = self.api.post(f"/api/orders/{order_id}/match/", {"radius_km": 1}, format="json")
        self.assertEqual(response.status_code, 200)

        self.api.force_authenticate(user=master_user)
        response = self.api.post(f"/api/orders/{order_id}/master-accept/", {}, format="json")
        self.assertEqual(response.status_code, 200)

        response = self.api.post(f"/api/orders/{order_id}/propose-price/", {"amount_uzs": 120_000}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["order"]["pending_price_proposal_uzs"], 120_000)

        self.api.force_authenticate(user=client)
        response = self.api.post(f"/api/orders/{order_id}/accept-price/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["order"]["status"], "master_on_way")
        self.assertEqual(response.json()["order"]["agreed_price_uzs"], 120_000)

        response = self.api.get("/api/notifications/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(item["event_type"] == "order.master_on_way" for item in response.json()))

        self.api.force_authenticate(user=master_user)
        for order_status in [OrderStatus.MASTER_ARRIVED, OrderStatus.IN_PROGRESS, OrderStatus.WORK_DONE]:
            response = self.api.post(f"/api/orders/{order_id}/status/", {"status": order_status}, format="json")
            self.assertEqual(response.status_code, 200)

        self.api.force_authenticate(user=client)
        response = self.api.post(f"/api/orders/{order_id}/status/", {"status": OrderStatus.COMPLETED}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "assigned_master_only_action")

        response = self.api.post(
            f"/api/orders/{order_id}/complete/",
            {"final_price_uzs": 120_000, "payment_method": "cash"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["order"]["status"], OrderStatus.COMPLETED)

        response = self.api.post(
            "/api/reviews/",
            {"order": order_id, "rating": 5, "tags": ["quality"], "text": "Good work"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["review"]["target_name"], "Master")

        response = self.api.post(
            "/api/orders/",
            {
                "category_id": self.category.id,
                "description": "Cancel this order",
                "address_text": "Tashkent, Cancel 3",
                "latitude": "41.312000",
                "longitude": "69.241000",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        cancellable_order_id = response.json()["id"]

        response = self.api.post(f"/api/orders/{cancellable_order_id}/match/", {"radius_km": 1}, format="json")
        self.assertEqual(response.status_code, 200)

        response = self.api.post(
            f"/api/orders/{cancellable_order_id}/cancel/",
            {"reason": "client_cancelled", "comment": "Client changed plans"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["order"]["status"], "cancelled")

        self.api.force_authenticate(user=master_user)
        response = self.api.get("/api/notifications/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            any(
                item["event_type"] == "order.cancelled"
                and item["payload"]["order_id"] == cancellable_order_id
                for item in response.json()
            )
        )

    def test_order_actions_reject_wrong_participant_role(self):
        client = User.objects.create_user(phone="+998901111111", full_name="Client")
        master_user = User.objects.create_user(phone="+998902222222", full_name="Master")
        master = MasterProfile.objects.create(
            user=master_user,
            status=MasterStatus.APPROVED,
            is_online=True,
            current_latitude=Decimal("41.311081"),
            current_longitude=Decimal("69.240562"),
            rating=Decimal("4.90"),
            activity_points=700,
        )
        MasterCategoryPrice.objects.create(
            master=master,
            category=self.category,
            min_price_uzs=80_000,
            max_price_uzs=250_000,
        )
        wallet = MasterWallet.objects.create(master=master)
        top_up_wallet(wallet, 40_001)
        subscription = activate_package(master, orders_count=10, days=30)

        self.api.force_authenticate(user=client)
        response = self.api.post(
            "/api/orders/",
            {
                "category_id": self.category.id,
                "description": "No light in room",
                "address_text": "Tashkent, Navoi 15",
                "latitude": "41.312000",
                "longitude": "69.241000",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        order_id = response.json()["id"]

        response = self.api.post(f"/api/orders/{order_id}/match/", {"radius_km": 1}, format="json")
        self.assertEqual(response.status_code, 200)

        response = self.api.post(f"/api/orders/{order_id}/master-accept/", {}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "master_profile_required")

        self.api.force_authenticate(user=master_user)
        response = self.api.post(f"/api/orders/{order_id}/match/", {"radius_km": 1}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "client_only_action")

        subscription.is_frozen = True
        subscription.save(update_fields=["is_frozen", "updated_at"])
        response = self.api.post(f"/api/orders/{order_id}/master-accept/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "master_not_eligible")

        subscription.is_frozen = False
        subscription.save(update_fields=["is_frozen", "updated_at"])
        response = self.api.post(f"/api/orders/{order_id}/master-accept/", {}, format="json")
        self.assertEqual(response.status_code, 200)

        self.api.force_authenticate(user=client)
        response = self.api.post(f"/api/orders/{order_id}/propose-price/", {"amount_uzs": 120_000}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "assigned_master_only_action")

        self.api.force_authenticate(user=master_user)
        response = self.api.post(f"/api/orders/{order_id}/propose-price/", {"amount_uzs": 120_000}, format="json")
        self.assertEqual(response.status_code, 200)

        response = self.api.post(f"/api/orders/{order_id}/accept-price/", {}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "client_only_action")

        self.api.force_authenticate(user=client)
        response = self.api.post(f"/api/orders/{order_id}/accept-price/", {}, format="json")
        self.assertEqual(response.status_code, 200)

        response = self.api.post(
            f"/api/orders/{order_id}/status/",
            {"status": OrderStatus.MASTER_ARRIVED},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "assigned_master_only_action")

        self.api.force_authenticate(user=master_user)
        response = self.api.post(f"/api/orders/{order_id}/cancel/", {"reason": "client_cancelled"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "client_only_action")

        for order_status in [OrderStatus.MASTER_ARRIVED, OrderStatus.IN_PROGRESS, OrderStatus.WORK_DONE]:
            response = self.api.post(f"/api/orders/{order_id}/status/", {"status": order_status}, format="json")
            self.assertEqual(response.status_code, 200)

        response = self.api.post(
            f"/api/orders/{order_id}/complete/",
            {"final_price_uzs": 120_000, "payment_method": "cash"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "client_only_action")

        self.api.force_authenticate(user=client)
        response = self.api.post(
            f"/api/orders/{order_id}/complete/",
            {"final_price_uzs": 120_000, "payment_method": "cash"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["order"]["status"], OrderStatus.COMPLETED)

    def test_client_can_complete_cash_order_when_master_package_counters_are_empty(self):
        client = User.objects.create_user(phone="+998901111111", full_name="Client")
        master_user = User.objects.create_user(phone="+998902222222", full_name="Master")
        master = MasterProfile.objects.create(
            user=master_user,
            status=MasterStatus.APPROVED,
            is_online=True,
            current_latitude=Decimal("41.311081"),
            current_longitude=Decimal("69.240562"),
        )
        wallet = MasterWallet.objects.create(
            master=master,
            free_orders_remaining=0,
            package_orders_remaining=0,
        )
        top_up_wallet(wallet, 40_001)
        order = Order.objects.create(
            client=client,
            master=master,
            category=self.category,
            status=OrderStatus.WORK_DONE,
            description="Cash completion",
            address_text="Tashkent, Navoi 15",
            latitude=Decimal("41.312000"),
            longitude=Decimal("69.241000"),
        )

        self.api.force_authenticate(user=client)
        response = self.api.post(
            f"/api/orders/{order.id}/complete/",
            {"final_price_uzs": 120_000, "payment_method": "click"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.WORK_DONE)

        response = self.api.post(
            f"/api/orders/{order.id}/complete/",
            {"final_price_uzs": 120_000, "payment_method": "cash"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["order"]["status"], OrderStatus.COMPLETED)
        wallet.refresh_from_db()
        self.assertEqual(wallet.free_orders_remaining, 0)
        self.assertEqual(wallet.package_orders_remaining, 0)

    @override_settings(
        CHANNEL_LAYERS={
            "default": {
                "BACKEND": "channels.layers.InMemoryChannelLayer",
            }
        }
    )
    def test_master_location_ping_broadcasts_order_event(self):
        client = User.objects.create_user(phone="+998901111111", full_name="Client")
        master_user = User.objects.create_user(phone="+998902222222", full_name="Master")
        master = MasterProfile.objects.create(
            user=master_user,
            status=MasterStatus.APPROVED,
            is_online=True,
            current_latitude=Decimal("41.311081"),
            current_longitude=Decimal("69.240562"),
        )
        order = Order.objects.create(
            client=client,
            master=master,
            category=self.category,
            status=OrderStatus.MASTER_ON_WAY,
            description="No light in room",
            address_text="Tashkent, Navoi 15",
            latitude=Decimal("41.312000"),
            longitude=Decimal("69.241000"),
        )
        channel_layer = get_channel_layer()
        channel_name = async_to_sync(channel_layer.new_channel)()
        async_to_sync(channel_layer.group_add)(f"order_{order.id}", channel_name)

        self.api.force_authenticate(user=master_user)
        response = self.api.post(
            "/api/geo/master-pings/ping/",
            {
                "order": str(order.id),
                "latitude": "41.313000",
                "longitude": "69.242000",
                "accuracy_meters": 12,
                "heading_degrees": 90,
                "speed_mps": "5.40",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(MasterLocationPing.objects.count(), 1)
        master.refresh_from_db()
        self.assertEqual(master.current_latitude, Decimal("41.313000"))
        self.assertEqual(master.current_longitude, Decimal("69.242000"))

        event = async_to_sync(channel_layer.receive)(channel_name)
        self.assertEqual(event["type"], "order.event")
        self.assertEqual(event["payload"]["event"], "order.master_location")
        self.assertEqual(event["payload"]["order_id"], str(order.id))
        self.assertEqual(event["payload"]["latitude"], "41.313000")
        self.assertEqual(event["payload"]["longitude"], "69.242000")

    def test_master_location_ping_rejects_unassigned_order(self):
        client = User.objects.create_user(phone="+998901111111", full_name="Client")
        master_user = User.objects.create_user(phone="+998902222222", full_name="Master")
        other_master_user = User.objects.create_user(phone="+998903333333", full_name="Other Master")
        master = MasterProfile.objects.create(user=master_user, status=MasterStatus.APPROVED)
        other_master = MasterProfile.objects.create(user=other_master_user, status=MasterStatus.APPROVED)
        order = Order.objects.create(
            client=client,
            master=other_master,
            category=self.category,
            status=OrderStatus.MASTER_ON_WAY,
            address_text="Tashkent, Navoi 15",
            latitude=Decimal("41.312000"),
            longitude=Decimal("69.241000"),
        )

        self.api.force_authenticate(user=master_user)
        response = self.api.post(
            "/api/geo/master-pings/ping/",
            {
                "order": str(order.id),
                "latitude": "41.313000",
                "longitude": "69.242000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "order_not_assigned_to_master")
        self.assertFalse(MasterLocationPing.objects.exists())

import json
import time
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock
from urllib.parse import unquote, urlparse

from asgiref.sync import async_to_sync
from asgiref.testing import ApplicationCommunicator as BaseApplicationCommunicator
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import MasterWallet
from apps.billing.services import activate_package, top_up_wallet
from apps.masters.models import MasterCategoryPrice, MasterProfile, MasterStatus, ServiceCategory
from apps.orders.models import MasterOffer, MasterOfferStatus, Order, OrderStatus
from apps.orders.services import MASTER_OFFER_TTL_SECONDS, expire_master_offer
from config.routing import websocket_urlpatterns


def _no_op():
    pass


class WebsocketCommunicator(BaseApplicationCommunicator):
    def __init__(self, application, path: str):
        parsed = urlparse(path)
        scope = {
            "type": "websocket",
            "path": unquote(parsed.path),
            "query_string": parsed.query.encode("utf-8"),
            "headers": [],
            "subprotocols": [],
        }
        super().__init__(application, scope)

    async def connect(self):
        await self.send_input({"type": "websocket.connect"})
        response = await self.receive_output(1)
        if response["type"] == "websocket.close":
            return False, response.get("code", 1000)
        return True, response.get("subprotocol")

    async def send_json_to(self, data: dict):
        await self.send_input({"type": "websocket.receive", "text": json.dumps(data)})

    async def receive_json_from(self, timeout=1):
        response = await self.receive_output(timeout)
        assert response["type"] == "websocket.send"
        return json.loads(response["text"])

    async def disconnect(self):
        await self.send_input({"type": "websocket.disconnect", "code": 1000})
        await self.wait(1)

    async def send_input(self, message):
        with mock.patch("channels.db.close_old_connections", _no_op):
            return await super().send_input(message)

    async def receive_output(self, timeout=1):
        with mock.patch("channels.db.close_old_connections", _no_op):
            return await super().receive_output(timeout)


@override_settings(MASTERGO_OFFER_EXPIRATION_TIMER_ENABLED=False)
class OfferRealtimeIntegrationTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.application = URLRouter(websocket_urlpatterns)
        self.category = ServiceCategory.objects.create(
            slug="electrician",
            name_ru="Электрик",
            name_uz="Elektrik",
            icon="bolt",
            sort_order=1,
        )
        self.client_user = User.objects.create_user(phone="+998901111111", full_name="Client")
        self.master_user = User.objects.create_user(phone="+998902222222", full_name="Master One")
        self.master = self._create_master(
            user=self.master_user,
            latitude=Decimal("41.311200"),
            longitude=Decimal("69.240600"),
            rating=Decimal("4.90"),
            activity_points=800,
        )
        self.master_token = Token.objects.create(user=self.master_user)

    def test_client_order_pushes_fullscreen_offer_and_master_accepts_via_ws(self):
        async_to_sync(self._client_order_pushes_fullscreen_offer_and_master_accepts_via_ws)()

    async def _client_order_pushes_fullscreen_offer_and_master_accepts_via_ws(self):
        communicator = WebsocketCommunicator(
            self.application,
            f"/ws/masters/orders/?token={self.master_token.key}",
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        started_at = time.monotonic()
        response = await database_sync_to_async(self._create_order_via_api)()
        self.assertEqual(response.status_code, 201, response.content)

        offer_payload = await self._receive_event(communicator, "offer")
        elapsed = time.monotonic() - started_at
        self.assertLess(elapsed, 1.0)
        self.assertEqual(offer_payload["type"], "offer")
        self.assertEqual(offer_payload["order_id"], response.json()["id"])
        self.assertIsNotNone(offer_payload["offer_id"])
        expires_at = datetime.fromisoformat(offer_payload["expires_at"])
        ttl_seconds = (expires_at - timezone.now()).total_seconds()
        self.assertGreater(ttl_seconds, MASTER_OFFER_TTL_SECONDS - 5)
        self.assertLessEqual(ttl_seconds, MASTER_OFFER_TTL_SECONDS)

        await communicator.send_json_to(
            {
                "action": "accept_offer",
                "offer_id": offer_payload["offer_id"],
                "order_id": offer_payload["order_id"],
            }
        )
        accepted_payload = await self._receive_event(communicator, "offer_accepted")
        self.assertEqual(accepted_payload["order"]["status"], OrderStatus.ACCEPTED_BY_MASTER)

        offer = await database_sync_to_async(MasterOffer.objects.get)(id=offer_payload["offer_id"])
        order = await database_sync_to_async(Order.objects.get)(id=response.json()["id"])
        self.assertEqual(offer.status, MasterOfferStatus.ACCEPTED)
        self.assertEqual(order.status, OrderStatus.ACCEPTED_BY_MASTER)
        await communicator.disconnect()

    def test_ignored_offer_expires_and_next_master_receives_ws_offer(self):
        fallback_user = User.objects.create_user(phone="+998903333333", full_name="Master Two")
        fallback_master = self._create_master(
            user=fallback_user,
            latitude=Decimal("41.311300"),
            longitude=Decimal("69.240700"),
            rating=Decimal("4.70"),
            activity_points=600,
        )
        fallback_token = Token.objects.create(user=fallback_user)
        async_to_sync(self._ignored_offer_expires_and_next_master_receives_ws_offer)(
            fallback_master.id,
            fallback_token.key,
        )

    async def _ignored_offer_expires_and_next_master_receives_ws_offer(self, fallback_master_id, fallback_token):
        first = WebsocketCommunicator(self.application, f"/ws/masters/orders/?token={self.master_token.key}")
        second = WebsocketCommunicator(self.application, f"/ws/masters/orders/?token={fallback_token}")
        self.assertTrue((await first.connect())[0])
        self.assertTrue((await second.connect())[0])

        response = await database_sync_to_async(self._create_order_via_api)()
        first_offer_payload = await self._receive_event(first, "offer")
        offer = await database_sync_to_async(MasterOffer.objects.get)(id=first_offer_payload["offer_id"])
        await database_sync_to_async(self._expire_offer_in_db)(offer.id)

        self.assertTrue(await database_sync_to_async(expire_master_offer)(offer.id))

        expired_payload = await self._receive_event(first, "offer_expired")
        next_offer_payload = await self._receive_event(second, "offer")
        self.assertEqual(expired_payload["offer_id"], offer.id)
        self.assertEqual(next_offer_payload["order_id"], response.json()["id"])
        self.assertEqual(
            (await database_sync_to_async(MasterOffer.objects.get)(id=next_offer_payload["offer_id"])).master_id,
            fallback_master_id,
        )
        order = await database_sync_to_async(Order.objects.get)(id=response.json()["id"])
        self.assertEqual(order.status, OrderStatus.OFFERED_TO_MASTER)
        self.assertEqual(order.master_id, fallback_master_id)
        await first.disconnect()
        await second.disconnect()

    async def _receive_event(self, communicator, event_type: str):
        for _ in range(5):
            payload = await communicator.receive_json_from(timeout=1)
            if payload.get("event") == event_type or payload.get("type") == event_type:
                return payload
        self.fail(f"Did not receive websocket event {event_type}")

    def _create_master(
        self,
        *,
        user,
        latitude: Decimal,
        longitude: Decimal,
        rating: Decimal,
        activity_points: int,
    ) -> MasterProfile:
        master = MasterProfile.objects.create(
            user=user,
            status=MasterStatus.APPROVED,
            is_online=True,
            current_latitude=latitude,
            current_longitude=longitude,
            rating=rating,
            activity_points=activity_points,
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
        return master

    def _create_order_via_api(self):
        api = APIClient()
        api.force_authenticate(user=self.client_user)
        return api.post(
            "/api/orders/",
            {
                "category_id": self.category.id,
                "description": "No light in the kitchen",
                "address_text": "Tashkent, Navoi 15",
                "latitude": "41.312000",
                "longitude": "69.241000",
            },
            format="json",
        )

    def _expire_offer_in_db(self, offer_id: int) -> None:
        offer = MasterOffer.objects.get(id=offer_id)
        offer.expires_at = timezone.now() - timedelta(seconds=1)
        offer.save(update_fields=["expires_at"])

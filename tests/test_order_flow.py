from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib import admin
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.test import RequestFactory
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import MasterWallet
from apps.billing.services import top_up_wallet
from apps.masters.models import MasterCategoryPrice, MasterProfile, MasterStatus, ServiceCategory
from apps.masters.services import master_can_receive_orders
from apps.notifications.models import NotificationEvent
from apps.orders.admin import OrderAdmin
from apps.orders.models import MasterOfferStatus, Order, OrderCancelReason, OrderEvent, OrderStatus, PriceProposalStatus
from apps.orders.services import (
    MASTER_OFFER_TTL_SECONDS,
    OrderActionError,
    accept_master_offer,
    accept_price,
    create_order_notifications,
    expire_master_offer,
    match_order_with_radius_expansion,
    offer_order_to_best_master,
    propose_price,
)


@override_settings(MASTERGO_OFFER_EXPIRATION_TIMER_ENABLED=False)
class OrderFlowTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            slug="electrician",
            name_ru="Электрик",
            name_uz="Elektrik",
            icon="⚡",
            sort_order=1,
        )
        self.client = User.objects.create_user(phone="+998901111111", full_name="Client")
        self.master_user = User.objects.create_user(phone="+998902222222", full_name="Master")
        self.master = MasterProfile.objects.create(
            user=self.master_user,
            status=MasterStatus.APPROVED,
            is_online=True,
            current_latitude=Decimal("41.311081"),
            current_longitude=Decimal("69.240562"),
            rating=Decimal("4.90"),
            activity_points=700,
        )
        MasterCategoryPrice.objects.create(
            master=self.master,
            category=self.category,
            min_price_uzs=80_000,
            max_price_uzs=250_000,
        )
        self.wallet = MasterWallet.objects.create(master=self.master)
        self.order = Order.objects.create(
            client=self.client,
            category=self.category,
            description="No light in room",
            address_text="Tashkent, Navoi 15",
            latitude=Decimal("41.312000"),
            longitude=Decimal("69.241000"),
        )

    def test_master_needs_balance_above_minimum_to_receive_orders(self):
        self.assertFalse(master_can_receive_orders(self.master))

        top_up_wallet(self.wallet, 40_000)
        self.assertFalse(master_can_receive_orders(self.master))

        top_up_wallet(self.wallet, 1)
        self.assertTrue(master_can_receive_orders(self.master))

    def test_master_with_active_order_cannot_receive_another_order(self):
        top_up_wallet(self.wallet, 40_001)
        self.assertTrue(master_can_receive_orders(self.master))

        active_order = Order.objects.create(
            client=self.client,
            master=self.master,
            category=self.category,
            status=OrderStatus.MASTER_ON_WAY,
            description="Already assigned order",
            address_text="Tashkent, Busy 1",
            latitude=Decimal("41.312000"),
            longitude=Decimal("69.241000"),
        )

        self.assertFalse(master_can_receive_orders(self.master))

        active_order.status = OrderStatus.COMPLETED
        active_order.save(update_fields=["status", "updated_at"])
        self.assertTrue(master_can_receive_orders(self.master))

    def test_order_can_match_master_accept_price_and_depart(self):
        top_up_wallet(self.wallet, 40_001)

        offer = offer_order_to_best_master(self.order, radius_km=1)
        self.assertIsNotNone(offer)
        ttl_seconds = (offer.expires_at - timezone.now()).total_seconds()
        self.assertGreater(ttl_seconds, MASTER_OFFER_TTL_SECONDS - 5)
        self.assertLessEqual(ttl_seconds, MASTER_OFFER_TTL_SECONDS)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.OFFERED_TO_MASTER)
        self.assertEqual(self.order.master, self.master)
        self.assertTrue(
            NotificationEvent.objects.filter(user=self.master_user, event_type="order.offered").exists()
        )

        order = accept_master_offer(offer)
        self.assertEqual(order.status, OrderStatus.ACCEPTED_BY_MASTER)
        self.assertTrue(
            NotificationEvent.objects.filter(user=self.client, event_type="order.master_accepted").exists()
        )

        proposal = propose_price(order, self.master, 120_000)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PRICE_PROPOSED)
        self.assertEqual(proposal.status, PriceProposalStatus.PENDING)
        self.assertTrue(
            NotificationEvent.objects.filter(user=self.client, event_type="order.price_proposed").exists()
        )

        order = accept_price(proposal, self.client)
        self.assertEqual(order.status, OrderStatus.MASTER_ON_WAY)
        self.assertEqual(order.agreed_price_uzs, 120_000)
        self.assertTrue(
            NotificationEvent.objects.filter(user=self.master_user, event_type="order.price_accepted").exists()
        )
        self.assertTrue(
            NotificationEvent.objects.filter(user=self.client, event_type="order.master_on_way").exists()
        )

    @patch("apps.notifications.push.send_push_to_user")
    def test_order_offer_push_uses_data_for_native_full_screen_alert(self, send_push):
        self.order.master = self.master
        self.order.status = OrderStatus.OFFERED_TO_MASTER
        self.order.save(update_fields=["master", "status", "updated_at"])

        create_order_notifications(
            self.order,
            OrderStatus.SEARCHING,
            OrderStatus.OFFERED_TO_MASTER,
        )

        send_push.assert_called_once()
        _, kwargs = send_push.call_args
        self.assertEqual(kwargs["data"]["event"], "order.offered")
        self.assertEqual(kwargs["data"]["order_id"], str(self.order.id))
        self.assertEqual(kwargs["channel_id"], "incoming_orders")
        self.assertEqual(kwargs["sound"], "incoming_call")
        self.assertIs(kwargs["include_notification"], False)

    @override_settings(
        STORAGES={
            "default": {
                "BACKEND": "django.core.files.storage.InMemoryStorage",
            },
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
            },
        }
    )
    def test_post_orders_accepts_schedule_budget_attachments_and_starts_matching(self):
        top_up_wallet(self.wallet, 40_001)
        scheduled_at = timezone.now() + timedelta(hours=2)
        first_photo = SimpleUploadedFile(
            "breaker.jpg",
            b"first image",
            content_type="image/jpeg",
        )
        second_photo = SimpleUploadedFile(
            "socket.jpg",
            b"second image",
            content_type="image/jpeg",
        )

        api = APIClient()
        api.force_authenticate(user=self.client)
        response = api.post(
            "/api/orders/",
            {
                "category_id": self.category.id,
                "description": "No light in the kitchen",
                "address_text": "Tashkent, Navoi 15",
                "latitude": "41.312000",
                "longitude": "69.241000",
                "scheduled_at": scheduled_at.isoformat(),
                "budget_ceiling_uzs": "150000",
                "attachments[]": [first_photo, second_photo],
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertEqual(payload["budget_ceiling_uzs"], 150_000)
        self.assertIsNotNone(payload["scheduled_at"])
        self.assertEqual(len(payload["attachments"]), 2)
        self.assertTrue(all(item["url"] for item in payload["attachments"]))

        order = Order.objects.get(id=payload["id"])
        self.assertEqual(order.attachments.count(), 2)
        self.assertEqual(order.status, OrderStatus.OFFERED_TO_MASTER)
        self.assertEqual(order.master, self.master)
        self.assertTrue(order.master_offers.filter(status=MasterOfferStatus.PENDING).exists())

    def test_master_cannot_accept_offer_after_balance_drops(self):
        top_up_wallet(self.wallet, 40_001)
        offer = offer_order_to_best_master(self.order, radius_km=1)
        self.wallet.balance_uzs = 10_000
        self.wallet.save(update_fields=["balance_uzs", "updated_at"])

        with self.assertRaises(OrderActionError) as context:
            accept_master_offer(offer)

        self.assertEqual(context.exception.code, "master_not_eligible")
        offer.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(offer.status, MasterOfferStatus.PENDING)
        self.assertEqual(self.order.status, OrderStatus.OFFERED_TO_MASTER)

    def test_master_cannot_accept_expired_offer(self):
        top_up_wallet(self.wallet, 40_001)
        offer = offer_order_to_best_master(self.order, radius_km=1)
        offer.expires_at = timezone.now() - timedelta(seconds=1)
        offer.save(update_fields=["expires_at"])

        with self.assertRaises(OrderActionError) as context:
            accept_master_offer(offer)

        self.assertEqual(context.exception.code, "offer_expired")
        offer.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(offer.status, MasterOfferStatus.PENDING)
        self.assertEqual(self.order.status, OrderStatus.OFFERED_TO_MASTER)

    def test_matching_expands_radius_until_candidate_found(self):
        self.master.is_online = False
        self.master.save(update_fields=["is_online", "updated_at"])

        far_master_user = User.objects.create_user(phone="+998904444444", full_name="Far Master")
        far_master = MasterProfile.objects.create(
            user=far_master_user,
            status=MasterStatus.APPROVED,
            is_online=True,
            current_latitude=Decimal("41.330000"),
            current_longitude=Decimal("69.241000"),
            rating=Decimal("4.80"),
            activity_points=600,
        )
        MasterCategoryPrice.objects.create(
            master=far_master,
            category=self.category,
            min_price_uzs=80_000,
            max_price_uzs=250_000,
        )
        far_wallet = MasterWallet.objects.create(master=far_master)
        top_up_wallet(far_wallet, 40_001)

        result = match_order_with_radius_expansion(self.order, start_radius_km=1)

        self.assertFalse(result.exhausted)
        self.assertIsNotNone(result.offer)
        self.assertEqual(result.offer.master, far_master)
        self.assertEqual(result.offer.radius_km, 3)
        self.assertEqual(result.attempted_radii_km, (1, 3, 6))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.OFFERED_TO_MASTER)

    def test_matching_skips_master_with_active_order(self):
        top_up_wallet(self.wallet, 40_001)
        Order.objects.create(
            client=self.client,
            master=self.master,
            category=self.category,
            status=OrderStatus.MASTER_ON_WAY,
            description="Already assigned order",
            address_text="Tashkent, Busy 1",
            latitude=Decimal("41.312000"),
            longitude=Decimal("69.241000"),
        )
        fallback_master_user = User.objects.create_user(phone="+998904444444", full_name="Fallback Master")
        fallback_master = MasterProfile.objects.create(
            user=fallback_master_user,
            status=MasterStatus.APPROVED,
            is_online=True,
            current_latitude=Decimal("41.311500"),
            current_longitude=Decimal("69.241000"),
            rating=Decimal("4.70"),
            activity_points=500,
        )
        MasterCategoryPrice.objects.create(
            master=fallback_master,
            category=self.category,
            min_price_uzs=80_000,
            max_price_uzs=250_000,
        )
        fallback_wallet = MasterWallet.objects.create(master=fallback_master)
        top_up_wallet(fallback_wallet, 40_001)

        offer = offer_order_to_best_master(self.order, radius_km=1)

        self.assertIsNotNone(offer)
        self.assertEqual(offer.master, fallback_master)

    def test_matching_keeps_order_searching_when_no_master_found_after_expansion(self):
        self.master.is_online = False
        self.master.save(update_fields=["is_online", "updated_at"])

        result = match_order_with_radius_expansion(self.order, start_radius_km=1)

        self.assertTrue(result.exhausted)
        self.assertIsNone(result.offer)
        self.assertEqual(result.attempted_radii_km, (1, 3, 6))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.SEARCHING)
        self.assertEqual(self.order.cancellation_reason, "")
        self.assertTrue(
            NotificationEvent.objects.filter(user=self.client, event_type="order.no_master_found").exists()
        )

    def test_matching_expires_stale_offer_and_assigns_next_searching_order(self):
        top_up_wallet(self.wallet, 40_001)
        first_result = match_order_with_radius_expansion(self.order, start_radius_km=1)
        self.assertIsNotNone(first_result.offer)

        first_result.offer.expires_at = timezone.now() - timedelta(seconds=1)
        first_result.offer.save(update_fields=["expires_at"])
        next_order = Order.objects.create(
            client=self.client,
            category=self.category,
            status=OrderStatus.SEARCHING,
            description="Second order",
            address_text="Tashkent, Next 2",
            latitude=Decimal("41.312100"),
            longitude=Decimal("69.241100"),
        )

        next_result = match_order_with_radius_expansion(next_order, start_radius_km=1)

        self.assertIsNotNone(next_result.offer)
        self.assertEqual(next_result.offer.master, self.master)
        first_result.offer.refresh_from_db()
        self.assertEqual(first_result.offer.status, MasterOfferStatus.EXPIRED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.SEARCHING)
        self.assertIsNone(self.order.master)

    def test_matching_pushes_offer_payload_to_master_channel(self):
        top_up_wallet(self.wallet, 40_001)
        channel_layer = get_channel_layer()
        channel_name = async_to_sync(channel_layer.new_channel)()
        async_to_sync(channel_layer.group_add)(f"master_user_{self.master_user.id}", channel_name)

        offer = offer_order_to_best_master(self.order, radius_km=1)

        payloads = [
            async_to_sync(channel_layer.receive)(channel_name)["payload"]
            for _ in range(3)
        ]
        offer_payload = next(payload for payload in payloads if payload["event"] == "offer")
        self.assertEqual(offer_payload["offer_id"], offer.id)
        self.assertEqual(offer_payload["order_id"], str(self.order.id))
        self.assertEqual(offer_payload["status"], MasterOfferStatus.PENDING)
        self.assertLessEqual(offer_payload["ttl_seconds"], MASTER_OFFER_TTL_SECONDS)
        self.assertGreaterEqual(offer_payload["ttl_seconds"], MASTER_OFFER_TTL_SECONDS - 2)
        self.assertEqual(offer_payload["order"]["id"], str(self.order.id))

    def test_expired_offer_pushes_event_and_matches_next_master(self):
        top_up_wallet(self.wallet, 40_001)
        fallback_user = User.objects.create_user(phone="+998905555555", full_name="Fallback Master")
        fallback_master = MasterProfile.objects.create(
            user=fallback_user,
            status=MasterStatus.APPROVED,
            is_online=True,
            current_latitude=Decimal("41.311200"),
            current_longitude=Decimal("69.240700"),
            rating=Decimal("4.80"),
            activity_points=600,
        )
        MasterCategoryPrice.objects.create(
            master=fallback_master,
            category=self.category,
            min_price_uzs=80_000,
            max_price_uzs=250_000,
        )
        fallback_wallet = MasterWallet.objects.create(master=fallback_master)
        top_up_wallet(fallback_wallet, 40_001)

        offer = offer_order_to_best_master(self.order, radius_km=1)
        channel_layer = get_channel_layer()
        channel_name = async_to_sync(channel_layer.new_channel)()
        async_to_sync(channel_layer.group_add)(f"master_user_{self.master_user.id}", channel_name)
        offer.expires_at = timezone.now() - timedelta(seconds=1)
        offer.save(update_fields=["expires_at"])

        self.assertTrue(expire_master_offer(offer.id))

        message = async_to_sync(channel_layer.receive)(channel_name)
        self.assertEqual(message["payload"]["event"], "offer_expired")
        self.assertEqual(message["payload"]["offer_id"], offer.id)
        offer.refresh_from_db()
        self.assertEqual(offer.status, MasterOfferStatus.EXPIRED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.OFFERED_TO_MASTER)
        self.assertEqual(self.order.master, fallback_master)
        self.assertTrue(
            self.order.master_offers.filter(master=fallback_master, status=MasterOfferStatus.PENDING).exists()
        )

    def test_admin_dispute_action_records_order_event(self):
        self.client.is_staff = True
        self.client.save(update_fields=["is_staff"])
        self.order.status = OrderStatus.MASTER_ON_WAY
        self.order.master = self.master
        self.order.save(update_fields=["status", "master", "updated_at"])

        request = RequestFactory().post("/admin/orders/order/")
        request.user = self.client
        order_admin = OrderAdmin(Order, admin.site)
        order_admin.message_user = lambda *args, **kwargs: None

        order_admin.mark_disputed(request, Order.objects.filter(id=self.order.id))

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.DISPUTED)
        event = OrderEvent.objects.get(order=self.order, to_status=OrderStatus.DISPUTED)
        self.assertEqual(event.from_status, OrderStatus.MASTER_ON_WAY)
        self.assertEqual(event.actor, self.client)
        self.assertEqual(event.reason, "admin_mark_disputed")

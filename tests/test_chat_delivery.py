from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import ChatRoom
from apps.masters.models import MasterProfile, MasterStatus, ServiceCategory
from apps.orders.models import Order, OrderStatus
from apps.orders.services import transition_order


@override_settings(
    CHANNEL_LAYERS={
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    }
)
class ChatDeliveryTests(TestCase):
    def test_send_echoes_client_message_id_for_optimistic_reconciliation(self):
        client = User.objects.create_user(phone="+998901111111", full_name="Client")
        master_user = User.objects.create_user(phone="+998902222222", full_name="Master")
        master = MasterProfile.objects.create(user=master_user, status=MasterStatus.APPROVED)
        category = ServiceCategory.objects.create(
            slug="electrician",
            name_ru="Электрик",
            name_uz="Elektrik",
        )
        order = Order.objects.create(
            client=client,
            master=master,
            category=category,
            status=OrderStatus.ACCEPTED_BY_MASTER,
            description="Нет света",
            address_text="Ташкент",
            latitude=Decimal("41.312000"),
            longitude=Decimal("69.241000"),
        )
        room = ChatRoom.objects.create(order=order)
        api = APIClient()
        api.force_authenticate(user=client)

        response = api.post(
            "/api/chat/messages/send/",
            {
                "room_id": str(room.id),
                "kind": "text",
                "text": "Буду через 10 минут",
                "client_message_id": "client-123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["message"]["client_message_id"], "client-123")
        self.assertEqual(response.json()["message"]["text"], "Буду через 10 минут")

    def test_cancelling_order_closes_room_and_rejects_new_messages(self):
        client = User.objects.create_user(phone="+998901111113", full_name="Client")
        master_user = User.objects.create_user(phone="+998902222224", full_name="Master")
        master = MasterProfile.objects.create(user=master_user, status=MasterStatus.APPROVED)
        category = ServiceCategory.objects.create(
            slug="plumber",
            name_ru="Сантехник",
            name_uz="Santexnik",
        )
        order = Order.objects.create(
            client=client,
            master=master,
            category=category,
            status=OrderStatus.ACCEPTED_BY_MASTER,
            description="Течет кран",
            address_text="Ташкент",
            latitude=Decimal("41.312000"),
            longitude=Decimal("69.241000"),
        )
        room = ChatRoom.objects.create(order=order)

        transition_order(
            order,
            OrderStatus.CANCELLED,
            actor=client,
            reason="client_cancelled",
        )

        room.refresh_from_db()
        self.assertIsNotNone(room.closed_at)
        api = APIClient()
        api.force_authenticate(user=master_user)
        response = api.post(
            "/api/chat/messages/send/",
            {"room_id": str(room.id), "kind": "text", "text": "Вы где?"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "chat_closed")

    def test_chat_is_not_created_before_master_accepts(self):
        client = User.objects.create_user(phone="+998901111115", full_name="Client")
        category = ServiceCategory.objects.create(
            slug="cleaner",
            name_ru="Уборка",
            name_uz="Tozalash",
        )
        order = Order.objects.create(
            client=client,
            category=category,
            status=OrderStatus.SEARCHING,
            description="Уборка квартиры",
            address_text="Ташкент",
            latitude=Decimal("41.312000"),
            longitude=Decimal("69.241000"),
        )
        api = APIClient()
        api.force_authenticate(user=client)

        response = api.post(
            "/api/chat/rooms/for-order/",
            {"order_id": str(order.id)},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "chat_not_available")
        self.assertFalse(ChatRoom.objects.filter(order=order).exists())

from unittest.mock import patch

from django.test import TestCase

from apps.accounts.models import User
from apps.notifications.models import DeviceToken
from apps.notifications.push import send_push_to_user


class PushNotificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+998909999999", full_name="Master")
        DeviceToken.objects.create(user=self.user, token="active-fcm-token")

    def test_visible_android_notification_uses_requested_channel_and_sound(self):
        with (
            patch("apps.notifications.push._get_app", return_value=object()),
            patch("firebase_admin.messaging.send", return_value="message-id") as send,
        ):
            delivered = send_push_to_user(
                self.user,
                title="Новая заявка",
                body="Электрик · Ташкент",
                data={"event": "order.offered", "order_id": "42"},
                channel_id="incoming_orders",
                sound="incoming_call",
                include_notification=True,
            )

        self.assertEqual(delivered, 1)
        message = send.call_args.args[0]
        self.assertEqual(message.notification.title, "Новая заявка")
        self.assertEqual(message.notification.body, "Электрик · Ташкент")
        self.assertEqual(message.data["order_id"], "42")
        self.assertEqual(message.android.priority, "high")
        self.assertEqual(message.android.notification.channel_id, "incoming_orders")
        self.assertEqual(message.android.notification.sound, "incoming_call")

import asyncio
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.notifications.push import enqueue_push_to_user
from apps.notifications.realtime import safe_group_send


class _SlowChannelLayer:
    async def group_send(self, group, message):
        await asyncio.sleep(0.1)


class BackendResilienceTests(TestCase):
    def test_readiness_reports_database_and_cache(self):
        response = APIClient().get("/api/ready/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ready",
                "components": {"database": True, "cache": True},
            },
        )
        self.assertIn("Server-Timing", response.headers)
        self.assertIn("X-Request-Id", response.headers)

    @override_settings(MASTERGO_REALTIME_SEND_TIMEOUT_SECONDS=0.01)
    @patch(
        "apps.notifications.realtime.get_channel_layer",
        return_value=_SlowChannelLayer(),
    )
    def test_realtime_timeout_does_not_fail_business_request(self, _layer):
        delivered = safe_group_send("order_1", {"type": "order.event"})

        self.assertFalse(delivered)

    @patch("apps.notifications.push._credentials_configured", return_value=True)
    @patch("apps.notifications.push._start_push_worker")
    @patch("apps.notifications.push._push_queue.put_nowait")
    def test_push_is_queued_instead_of_sent_in_request(
        self,
        put_nowait,
        _start_worker,
        _configured,
    ):
        user = User.objects.create_user(phone="+998900000001")

        queued = enqueue_push_to_user(
            user,
            title="Title",
            body="Body",
            data={"event": "test"},
        )

        self.assertTrue(queued)
        put_nowait.assert_called_once()
        self.assertEqual(put_nowait.call_args.args[0]["user_id"], user.id)

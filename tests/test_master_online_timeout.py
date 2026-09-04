from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.masters.models import MasterProfile, MasterStatus
from apps.masters.services import expire_stale_online_sessions
from apps.notifications.models import NotificationEvent


@override_settings(MASTERGO_MAX_ONLINE_HOURS=8)
class MasterOnlineTimeoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+998909003301", full_name="Master"
        )
        self.master = MasterProfile.objects.create(
            user=self.user,
            status=MasterStatus.APPROVED,
            is_online=True,
            online_since=timezone.now() - timedelta(hours=8, minutes=1),
        )

    def test_expired_online_session_goes_offline_and_notifies_master(self):
        expired = expire_stale_online_sessions(now=timezone.now())

        self.master.refresh_from_db()
        self.assertEqual(expired, 1)
        self.assertFalse(self.master.is_online)
        self.assertIsNone(self.master.online_since)
        self.assertTrue(
            NotificationEvent.objects.filter(
                user=self.user, event_type="master.auto_offline"
            ).exists()
        )

    def test_recent_online_session_stays_online(self):
        self.master.online_since = timezone.now() - timedelta(hours=7, minutes=59)
        self.master.save(update_fields=["online_since", "updated_at"])

        expired = expire_stale_online_sessions(now=timezone.now())

        self.master.refresh_from_db()
        self.assertEqual(expired, 0)
        self.assertTrue(self.master.is_online)

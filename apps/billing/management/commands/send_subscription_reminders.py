from django.core.management.base import BaseCommand

from apps.billing.models import MasterSubscription
from apps.notifications.push import send_push_to_user


# Thresholds → marker. Expiry is checked in days, quota in remaining orders.
DAY_THRESHOLDS = {7: "d7", 3: "d3", 1: "d1"}
ORDER_THRESHOLDS = {10: "o10", 5: "o5", 0: "o0"}


class Command(BaseCommand):
    """Send package expiry / low-quota reminders (TZ §2.5). Run daily by cron.
    A per-subscription marker prevents re-sending the same nudge."""

    help = "Push reminders to masters whose package is running low on days or orders."

    def handle(self, *args, **options):
        sent = 0
        subs = MasterSubscription.objects.select_related("master__user").filter(
            is_frozen=False, expires_at__isnull=False
        )
        for sub in subs:
            if sub.is_expired or sub.orders_remaining <= 0:
                marker, title, body = self._exhausted_message(sub)
            else:
                marker, title, body = self._threshold_message(sub)
            if marker is None or marker == sub.reminder_marker:
                continue
            delivered = send_push_to_user(
                sub.master.user,
                title=title,
                body=body,
                data={"event": "subscription.reminder"},
                channel_id="reminders",
                sound="default",
                include_notification=True,
            )
            sub.reminder_marker = marker
            sub.save(update_fields=["reminder_marker", "updated_at"])
            sent += delivered
        self.stdout.write(self.style.SUCCESS(f"Reminder pushes delivered: {sent}."))

    def _exhausted_message(self, sub):
        if sub.orders_remaining <= 0:
            return "o0", "Пакет закончился", "Купите новый пакет, чтобы получать заказы."
        return "expired", "Срок пакета истёк", "Купите новый пакет, чтобы снова выходить онлайн."

    def _threshold_message(self, sub):
        # Prefer the more urgent of the two signals.
        for days, marker in DAY_THRESHOLDS.items():
            if sub.days_left == days:
                return marker, "Пакет скоро закончится", f"Осталось {days} дн. действия пакета."
        for orders, marker in ORDER_THRESHOLDS.items():
            if orders > 0 and sub.orders_remaining == orders:
                return marker, "Заказы заканчиваются", f"Осталось {orders} заказов в пакете."
        return None, "", ""

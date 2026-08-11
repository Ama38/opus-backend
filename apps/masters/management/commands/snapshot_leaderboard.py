from django.conf import settings
from django.core.management.base import BaseCommand

from apps.masters.models import MasterProfile, MasterStatus


class Command(BaseCommand):
    """Recompute the weekly leaderboard ranking (TZ §7.2). Run by a scheduler
    (e.g. weekly cron). Stores the previous rank so the app can show ↑/↓."""

    help = "Recompute master leaderboard ranks and remember the previous ones."

    def handle(self, *args, **options):
        threshold = int(getattr(settings, "MASTERGO_NEWCOMER_ORDER_THRESHOLD", 10))
        qualifying = list(
            MasterProfile.objects.filter(
                status=MasterStatus.APPROVED,
                completed_orders_count__gte=threshold,
            ).order_by("-completed_orders_count", "-rating", "id")
        )
        updated = 0
        for index, master in enumerate(qualifying, start=1):
            master.leaderboard_rank_prev = master.leaderboard_rank
            master.leaderboard_rank = index
            master.save(update_fields=["leaderboard_rank", "leaderboard_rank_prev", "updated_at"])
            updated += 1

        # Masters that dropped out of the qualifying set keep their prev rank but
        # lose the current one.
        for master in MasterProfile.objects.exclude(
            id__in=[m.id for m in qualifying]
        ).filter(leaderboard_rank__isnull=False):
            master.leaderboard_rank_prev = master.leaderboard_rank
            master.leaderboard_rank = None
            master.save(update_fields=["leaderboard_rank", "leaderboard_rank_prev", "updated_at"])

        self.stdout.write(self.style.SUCCESS(f"Leaderboard ranked {updated} master(s)."))

from django.core.management.base import BaseCommand

from apps.masters.services import snapshot_leaderboard


class Command(BaseCommand):
    """Recompute the weekly leaderboard ranking (TZ §7.2).

    Also runs automatically from the in-process sweeper (see
    apps.orders.sweeper) on MASTERGO_LEADERBOARD_SNAPSHOT_INTERVAL_HOURS, so
    this command is only needed for a manual/on-demand recompute.
    """

    help = "Recompute master leaderboard ranks and remember the previous ones."

    def handle(self, *args, **options):
        updated = snapshot_leaderboard()
        self.stdout.write(self.style.SUCCESS(f"Leaderboard ranked {updated} master(s)."))

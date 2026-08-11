from django.core.management.base import BaseCommand

from apps.billing.models import Package


PACKAGES = [
    ("starter", "Стартовый", "Boshlang'ich", 50, 200_000, 1),
    ("standard", "Стандарт", "Standart", 100, 300_000, 2),
]


class Command(BaseCommand):
    help = "Seed MasterGo subscription packages from the PRD (v3)."

    def handle(self, *args, **options):
        for slug, name_ru, name_uz, orders_count, price_uzs, sort_order in PACKAGES:
            Package.objects.update_or_create(
                slug=slug,
                defaults={
                    "name_ru": name_ru,
                    "name_uz": name_uz,
                    "orders_count": orders_count,
                    "price_uzs": price_uzs,
                    "sort_order": sort_order,
                    "is_active": True,
                },
            )
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(PACKAGES)} packages."))

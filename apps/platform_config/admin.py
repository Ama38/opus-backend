from django.contrib import admin

from .models import PlatformSettings


@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    list_display = [
        "__str__",
        "match_weight_distance",
        "match_weight_rating",
        "match_weight_completion",
        "match_weight_reaction",
        "updated_at",
    ]
    readonly_fields = ["updated_at"]
    fieldsets = [
        (
            "Веса матчинга (сумма ≈ 1.0)",
            {
                "fields": [
                    "match_weight_distance",
                    "match_weight_rating",
                    "match_weight_completion",
                    "match_weight_reaction",
                ],
                "description": "Как система ранжирует мастеров при подборе. Больше вес — сильнее влияет фактор.",
            },
        ),
        (None, {"fields": ["updated_at"]}),
    ]

    def has_add_permission(self, request):
        # Singleton: allow creating the first row, block the rest.
        return not PlatformSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

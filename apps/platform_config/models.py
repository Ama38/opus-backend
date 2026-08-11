from django.conf import settings
from django.core.cache import cache
from django.db import models


CACHE_KEY = "platform_settings_singleton"


class PlatformSettings(models.Model):
    """Operator-editable platform knobs. A single row (pk=1) holds the live
    values; every field falls back to the corresponding Django setting when the
    row does not exist yet, so the code keeps working before the first save."""

    singleton_id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    # Matching score weights (should sum to ~1.0; not strictly enforced).
    match_weight_distance = models.FloatField(default=0.50, verbose_name="Вес: расстояние")
    match_weight_rating = models.FloatField(default=0.30, verbose_name="Вес: рейтинг")
    match_weight_completion = models.FloatField(default=0.10, verbose_name="Вес: завершённость")
    match_weight_reaction = models.FloatField(default=0.10, verbose_name="Вес: скорость реакции")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Настройки платформы"
        verbose_name_plural = "Настройки платформы"

    def __str__(self) -> str:
        return "Настройки платформы"

    def save(self, *args, **kwargs):
        self.singleton_id = 1
        super().save(*args, **kwargs)
        cache.delete(CACHE_KEY)

    @classmethod
    def load(cls) -> "PlatformSettings":
        """Return the singleton, creating it from Django-settings defaults on
        first access. Cached to keep the matching hot-path cheap."""
        cached = cache.get(CACHE_KEY)
        if cached is not None:
            return cached
        obj, _ = cls.objects.get_or_create(
            singleton_id=1,
            defaults={
                "match_weight_distance": float(getattr(settings, "MASTERGO_MATCH_WEIGHT_DISTANCE", 0.50)),
                "match_weight_rating": float(getattr(settings, "MASTERGO_MATCH_WEIGHT_RATING", 0.30)),
                "match_weight_completion": float(getattr(settings, "MASTERGO_MATCH_WEIGHT_COMPLETION", 0.10)),
                "match_weight_reaction": float(getattr(settings, "MASTERGO_MATCH_WEIGHT_REACTION", 0.10)),
            },
        )
        cache.set(CACHE_KEY, obj, timeout=300)
        return obj

from django.db import models


class MasterLocationPing(models.Model):
    master = models.ForeignKey(
        "masters.MasterProfile", on_delete=models.CASCADE, related_name="location_pings", verbose_name="Мастер"
    )
    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="master_pings",
        verbose_name="Заказ",
    )
    latitude = models.DecimalField(max_digits=9, decimal_places=6, verbose_name="Широта")
    longitude = models.DecimalField(max_digits=9, decimal_places=6, verbose_name="Долгота")
    accuracy_meters = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Точность, м")
    heading_degrees = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Курс, °")
    speed_mps = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, verbose_name="Скорость, м/с"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Координата мастера"
        verbose_name_plural = "Координаты мастеров"

    def __str__(self) -> str:
        return f"{self.master} / {self.latitude},{self.longitude}"


class MapProvider(models.TextChoices):
    OSM = "osm", "OpenStreetMap"
    MOCK = "mock", "Заглушка"


class GeoProviderEvent(models.Model):
    provider = models.CharField(
        max_length=32, choices=MapProvider.choices, default=MapProvider.OSM, verbose_name="Провайдер"
    )
    event_type = models.CharField(max_length=80, verbose_name="Тип события")
    payload = models.JSONField(default=dict, blank=True, verbose_name="Доп. данные")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Событие геосервиса"
        verbose_name_plural = "События геосервиса"

    def __str__(self) -> str:
        return f"{self.provider} / {self.event_type}"

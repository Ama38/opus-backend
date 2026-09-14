from django.conf import settings
from django.db import models


class NotificationChannel(models.TextChoices):
    PUSH = "push", "Push"
    SMS = "sms", "SMS"
    IN_APP = "in_app", "В приложении"
    LOG = "log", "Лог"


class NotificationStatus(models.TextChoices):
    PENDING = "pending", "Ожидает"
    SENT = "sent", "Отправлено"
    FAILED = "failed", "Ошибка"
    SKIPPED = "skipped", "Пропущено"


class DevicePlatform(models.TextChoices):
    ANDROID = "android", "Android"
    IOS = "ios", "iOS"
    WEB = "web", "Web"


class DeviceToken(models.Model):
    """An FCM registration token for push delivery to a specific device."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="device_tokens", verbose_name="Пользователь"
    )
    token = models.CharField(max_length=255, unique=True, verbose_name="Токен")
    platform = models.CharField(
        max_length=16, choices=DevicePlatform.choices, default=DevicePlatform.ANDROID, verbose_name="Платформа"
    )
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлён")

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Токен устройства"
        verbose_name_plural = "Токены устройств"

    def __str__(self) -> str:
        return f"{self.user} / {self.platform} / {self.token[:12]}…"


class NotificationEvent(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_events",
        verbose_name="Пользователь",
    )
    channel = models.CharField(
        max_length=32, choices=NotificationChannel.choices, default=NotificationChannel.IN_APP, verbose_name="Канал"
    )
    event_type = models.CharField(max_length=80, verbose_name="Тип события")
    title = models.CharField(max_length=180, verbose_name="Заголовок")
    body = models.TextField(blank=True, verbose_name="Текст")
    payload = models.JSONField(default=dict, blank=True, verbose_name="Доп. данные")
    status = models.CharField(
        max_length=32, choices=NotificationStatus.choices, default=NotificationStatus.PENDING, verbose_name="Статус"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name="Отправлено")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Уведомление"
        verbose_name_plural = "Уведомления"

    def __str__(self) -> str:
        return f"{self.user} / {self.event_type} / {self.status}"


from django.conf import settings
from django.db import models


class SupportCaseStatus(models.TextChoices):
    OPEN = "open", "Открыто"
    IN_PROGRESS = "in_progress", "В работе"
    RESOLVED = "resolved", "Решено"
    CLOSED = "closed", "Закрыто"


class SupportCasePriority(models.TextChoices):
    LOW = "low", "Низкий"
    NORMAL = "normal", "Обычный"
    HIGH = "high", "Высокий"
    URGENT = "urgent", "Срочный"


class SupportCase(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="support_cases", verbose_name="Пользователь"
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_cases",
        verbose_name="Заказ",
    )
    status = models.CharField(
        max_length=32, choices=SupportCaseStatus.choices, default=SupportCaseStatus.OPEN, verbose_name="Статус"
    )
    priority = models.CharField(
        max_length=32,
        choices=SupportCasePriority.choices,
        default=SupportCasePriority.NORMAL,
        verbose_name="Приоритет",
    )
    subject = models.CharField(max_length=180, verbose_name="Тема")
    body = models.TextField(blank=True, verbose_name="Текст")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_support_cases",
        verbose_name="Назначено",
    )
    last_user_message_at = models.DateTimeField(null=True, blank=True, verbose_name="Последнее сообщение пользователя")
    last_operator_message_at = models.DateTimeField(null=True, blank=True, verbose_name="Последнее сообщение оператора")
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name="Закрыто")
    close_reason = models.CharField(max_length=64, blank=True, verbose_name="Причина закрытия")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Обращение в поддержку"
        verbose_name_plural = "Обращения в поддержку"

    def __str__(self) -> str:
        return self.subject


class SupportMessage(models.Model):
    case = models.ForeignKey(SupportCase, on_delete=models.CASCADE, related_name="messages", verbose_name="Обращение")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="support_messages", verbose_name="Отправитель"
    )
    text = models.TextField(verbose_name="Текст")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Отправлено")

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Сообщение поддержки"
        verbose_name_plural = "Сообщения поддержки"

    def __str__(self) -> str:
        return f"{self.case_id} / {self.sender}"


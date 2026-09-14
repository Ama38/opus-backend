import uuid

from django.conf import settings
from django.db import models


class MessageKind(models.TextChoices):
    TEXT = "text", "Текст"
    PHOTO = "photo", "Фото"
    VIDEO = "video", "Видео"
    PRICE_PROPOSAL = "price_proposal", "Предложение цены"
    SYSTEM = "system", "Системное"


class ChatRoom(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="chat_room", verbose_name="Заказ"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name="Закрыт")

    class Meta:
        verbose_name = "Чат"
        verbose_name_plural = "Чаты"

    def __str__(self) -> str:
        return f"Chat for {self.order_id}"


class ChatMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name="messages", verbose_name="Чат")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="chat_messages", verbose_name="Отправитель"
    )
    kind = models.CharField(max_length=32, choices=MessageKind.choices, default=MessageKind.TEXT, verbose_name="Тип")
    text = models.TextField(blank=True, verbose_name="Текст")
    attachment_url = models.URLField(blank=True, verbose_name="Вложение")
    price_uzs = models.PositiveIntegerField(null=True, blank=True, verbose_name="Цена, UZS")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Отправлено")
    read_at = models.DateTimeField(null=True, blank=True, verbose_name="Прочитано")

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Сообщение"
        verbose_name_plural = "Сообщения"

    def __str__(self) -> str:
        return f"{self.room_id} / {self.sender} / {self.kind}"


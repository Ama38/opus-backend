from django.conf import settings
from django.db import models


class Review(models.Model):
    order = models.ForeignKey(
        "orders.Order", on_delete=models.CASCADE, related_name="reviews", verbose_name="Заказ"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reviews_written", verbose_name="Автор"
    )
    target = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reviews_received", verbose_name="Кому"
    )
    rating = models.PositiveSmallIntegerField(verbose_name="Оценка")
    tags = models.JSONField(default=list, blank=True, verbose_name="Теги")
    text = models.TextField(blank=True, verbose_name="Текст")
    photo_urls = models.JSONField(default=list, blank=True, verbose_name="Фото")
    is_public = models.BooleanField(default=True, verbose_name="Публичный")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")

    class Meta:
        ordering = ["-created_at"]
        unique_together = ["order", "author", "target"]
        verbose_name = "Отзыв"
        verbose_name_plural = "Отзывы"

    def __str__(self) -> str:
        return f"{self.order_id} / {self.author} -> {self.target}: {self.rating}"


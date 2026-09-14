import uuid

from django.conf import settings
from django.db import models


class OrderStatus(models.TextChoices):
    DRAFT = "draft", "Черновик"
    SEARCHING = "searching", "Идёт поиск"
    OFFERED_TO_MASTER = "offered_to_master", "Предложен мастеру"
    ACCEPTED_BY_MASTER = "accepted_by_master", "Принят мастером"
    PRICE_PROPOSED = "price_proposed", "Предложена цена"
    PRICE_ACCEPTED = "price_accepted", "Цена согласована"
    MASTER_ON_WAY = "master_on_way", "Мастер в пути"
    MASTER_ARRIVED = "master_arrived", "Мастер на месте"
    IN_PROGRESS = "in_progress", "В работе"
    WORK_DONE = "work_done", "Работа выполнена"
    COMPLETED = "completed", "Завершён"
    CANCELLED = "cancelled", "Отменён"
    EXPIRED = "expired", "Истёк"
    DISPUTED = "disputed", "Спорный"


class OrderCancelReason(models.TextChoices):
    CLIENT_CANCELLED = "client_cancelled", "Отменил клиент"
    MASTER_DECLINED = "master_declined", "Отказал мастер"
    PRICE_REJECTED = "price_rejected", "Цена отклонена"
    MASTER_NO_RESPONSE = "master_no_response", "Мастер не ответил"
    NO_MASTER_FOUND = "no_master_found", "Мастер не найден"
    REPLACED_BY_NEW_ORDER = "replaced_by_new_order", "Заменён новым заказом"
    SYSTEM = "system", "Системная отмена"


class Order(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="client_orders", verbose_name="Клиент"
    )
    master = models.ForeignKey(
        "masters.MasterProfile",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="Мастер",
    )
    category = models.ForeignKey(
        "masters.ServiceCategory", on_delete=models.PROTECT, related_name="orders", verbose_name="Категория"
    )
    # Client picked a specific master from the directory: they get the first
    # (direct) offer; if they decline/expire, matching falls back to normal.
    preferred_master = models.ForeignKey(
        "masters.MasterProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="preferred_orders",
        verbose_name="Выбранный мастер",
    )
    status = models.CharField(
        max_length=32, choices=OrderStatus.choices, default=OrderStatus.DRAFT, verbose_name="Статус"
    )

    description = models.TextField(blank=True, verbose_name="Описание")
    address_text = models.CharField(max_length=255, verbose_name="Адрес")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, verbose_name="Широта")
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, verbose_name="Долгота")
    scheduled_at = models.DateTimeField(null=True, blank=True, verbose_name="Запланировано на")
    budget_ceiling_uzs = models.PositiveIntegerField(null=True, blank=True, verbose_name="Бюджет клиента")
    # Client doesn't mind the price and is happy for the master to estimate on site.
    price_flexible = models.BooleanField(default=False, verbose_name="Цена гибкая")

    agreed_price_uzs = models.PositiveIntegerField(null=True, blank=True, verbose_name="Согласованная цена")
    final_price_uzs = models.PositiveIntegerField(null=True, blank=True, verbose_name="Итоговая цена")
    payment_method = models.CharField(max_length=32, blank=True, verbose_name="Способ оплаты")

    cancellation_reason = models.CharField(
        max_length=64, choices=OrderCancelReason.choices, blank=True, verbose_name="Причина отмены"
    )
    cancellation_comment = models.TextField(blank=True, verbose_name="Комментарий к отмене")

    # How many times the client asked for a different master on this order. After
    # the configured threshold the order is routed to an operator.
    client_refusals = models.PositiveSmallIntegerField(default=0, verbose_name="Отказов клиента")
    needs_operator = models.BooleanField(default=False, verbose_name="Нужен оператор")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлён")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="Завершён")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"

    def __str__(self) -> str:
        return f"{self.category} / {self.client} / {self.status}"


class OrderAttachment(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="attachments", verbose_name="Заказ")
    file = models.ImageField(upload_to="orders/%Y/%m/", verbose_name="Файл")
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="Загружен")

    class Meta:
        ordering = ["uploaded_at"]
        verbose_name = "Вложение заказа"
        verbose_name_plural = "Вложения заказов"

    def __str__(self) -> str:
        return f"{self.order_id} / {self.file.name}"


class MasterOfferStatus(models.TextChoices):
    PENDING = "pending", "Ожидает"
    ACCEPTED = "accepted", "Принято"
    DECLINED = "declined", "Отклонено"
    EXPIRED = "expired", "Истекло"


class MasterOffer(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="master_offers", verbose_name="Заказ")
    master = models.ForeignKey(
        "masters.MasterProfile", on_delete=models.CASCADE, related_name="order_offers", verbose_name="Мастер"
    )
    status = models.CharField(
        max_length=32, choices=MasterOfferStatus.choices, default=MasterOfferStatus.PENDING, verbose_name="Статус"
    )
    score = models.DecimalField(max_digits=8, decimal_places=4, default=0, verbose_name="Скор")
    radius_km = models.PositiveSmallIntegerField(default=1, verbose_name="Радиус, км")
    expires_at = models.DateTimeField(verbose_name="Истекает")
    responded_at = models.DateTimeField(null=True, blank=True, verbose_name="Ответ получен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Предложение мастеру"
        verbose_name_plural = "Предложения мастерам"

    def __str__(self) -> str:
        return f"{self.order_id} -> {self.master} / {self.status}"


class PriceProposalStatus(models.TextChoices):
    PENDING = "pending", "Ожидает"
    ACCEPTED = "accepted", "Принято"
    REJECTED = "rejected", "Отклонено"
    WITHDRAWN = "withdrawn", "Отозвано"


class PriceProposal(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="price_proposals", verbose_name="Заказ")
    master = models.ForeignKey(
        "masters.MasterProfile", on_delete=models.PROTECT, related_name="price_proposals", verbose_name="Мастер"
    )
    amount_uzs = models.PositiveIntegerField(verbose_name="Сумма, UZS")
    status = models.CharField(
        max_length=32,
        choices=PriceProposalStatus.choices,
        default=PriceProposalStatus.PENDING,
        verbose_name="Статус",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    responded_at = models.DateTimeField(null=True, blank=True, verbose_name="Ответ получен")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Предложение цены"
        verbose_name_plural = "Предложения цены"

    def __str__(self) -> str:
        return f"{self.order_id}: {self.amount_uzs} UZS / {self.status}"


class OrderEvent(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="events", verbose_name="Заказ")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Инициатор"
    )
    from_status = models.CharField(max_length=32, blank=True, verbose_name="Из статуса")
    to_status = models.CharField(max_length=32, verbose_name="В статус")
    reason = models.CharField(max_length=120, blank=True, verbose_name="Причина")
    metadata = models.JSONField(default=dict, blank=True, verbose_name="Доп. данные")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Событие заказа"
        verbose_name_plural = "События заказов"

    def __str__(self) -> str:
        return f"{self.order_id}: {self.from_status} -> {self.to_status}"

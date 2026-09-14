from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.masters.models import MasterProfile


class Package(models.Model):
    """Admin-configurable subscription package: a bundle of order slots."""

    slug = models.SlugField(unique=True, verbose_name="Slug")
    name_ru = models.CharField(max_length=120, verbose_name="Название (рус)")
    name_uz = models.CharField(max_length=120, verbose_name="Название (узб)")
    orders_count = models.PositiveIntegerField(verbose_name="Кол-во заказов")
    price_uzs = models.PositiveIntegerField(verbose_name="Цена, UZS")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    sort_order = models.PositiveSmallIntegerField(default=100, verbose_name="Порядок")

    class Meta:
        ordering = ["sort_order", "orders_count"]
        verbose_name = "Пакет"
        verbose_name_plural = "Пакеты"

    def __str__(self) -> str:
        return f"{self.name_ru} ({self.orders_count})"


class MasterSubscription(models.Model):
    """The master's current order allowance. One per master.

    Replaces the old money wallet as the gate for going online / receiving
    orders: a master can work while ``orders_remaining > 0`` and the package has
    not expired (and is not frozen).
    """

    master = models.OneToOneField(
        MasterProfile, on_delete=models.CASCADE, related_name="subscription", verbose_name="Мастер"
    )
    orders_remaining = models.PositiveIntegerField(default=0, verbose_name="Осталось заказов")
    activated_at = models.DateTimeField(null=True, blank=True, verbose_name="Активирована")
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name="Истекает")
    is_frozen = models.BooleanField(default=False, verbose_name="Заморожена")
    frozen_at = models.DateTimeField(null=True, blank=True, verbose_name="Заморожена с")
    # Last expiry/quota reminder already delivered (e.g. "d7", "o5") — prevents
    # the daily reminder job from re-sending the same nudge (TZ §2.5).
    reminder_marker = models.CharField(max_length=8, blank=True, verbose_name="Метка напоминания")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создана")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлена")

    class Meta:
        verbose_name = "Подписка мастера"
        verbose_name_plural = "Подписки мастеров"

    def __str__(self) -> str:
        return f"{self.master}: {self.orders_remaining} orders"

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def is_active(self) -> bool:
        return (
            not self.is_frozen
            and self.orders_remaining > 0
            and self.expires_at is not None
            and not self.is_expired
        )

    @property
    def days_left(self) -> int:
        if self.expires_at is None:
            return 0
        delta = self.expires_at - timezone.now()
        return max(0, delta.days + (1 if delta.seconds else 0))


class PackagePurchaseStatus(models.TextChoices):
    PENDING = "pending", "Ожидает"
    ACTIVATED = "activated", "Активирован"
    REJECTED = "rejected", "Отклонён"


class PackagePurchase(models.Model):
    """A request to buy/activate a package. Payment happens offline; an operator
    activates it in admin, which applies the orders to the subscription."""

    master = models.ForeignKey(
        MasterProfile, on_delete=models.CASCADE, related_name="package_purchases", verbose_name="Мастер"
    )
    package = models.ForeignKey(
        Package, on_delete=models.PROTECT, related_name="purchases", null=True, blank=True, verbose_name="Пакет"
    )
    orders_count = models.PositiveIntegerField(verbose_name="Кол-во заказов")
    price_uzs = models.PositiveIntegerField(default=0, verbose_name="Цена, UZS")
    is_free = models.BooleanField(default=False, verbose_name="Бесплатно")
    status = models.CharField(
        max_length=16,
        choices=PackagePurchaseStatus.choices,
        default=PackagePurchaseStatus.PENDING,
        verbose_name="Статус",
    )
    note = models.TextField(blank=True, verbose_name="Заметка")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создана")
    activated_at = models.DateTimeField(null=True, blank=True, verbose_name="Активирована")
    activated_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activated_package_purchases",
        verbose_name="Активировал",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Заявка на пакет"
        verbose_name_plural = "Заявки на пакеты"

    def __str__(self) -> str:
        return f"{self.master} / {self.orders_count} / {self.status}"


class LedgerEntryType(models.TextChoices):
    MANUAL_TOP_UP = "manual_top_up", "Ручное пополнение"
    PACKAGE_PURCHASE = "package_purchase", "Покупка пакета"
    ORDER_DEBIT = "order_debit", "Списание за заказ"
    ADJUSTMENT = "adjustment", "Корректировка"


class MasterWallet(models.Model):
    master = models.OneToOneField(
        MasterProfile, on_delete=models.CASCADE, related_name="wallet", verbose_name="Мастер"
    )
    balance_uzs = models.PositiveIntegerField(default=0, verbose_name="Баланс, UZS")
    package_orders_remaining = models.PositiveIntegerField(default=0, verbose_name="Заказов по пакету")
    free_orders_remaining = models.PositiveIntegerField(default=10, verbose_name="Бесплатных заказов")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлён")

    class Meta:
        verbose_name = "Кошелёк мастера"
        verbose_name_plural = "Кошельки мастеров"

    def __str__(self) -> str:
        return f"{self.master}: {self.balance_uzs} UZS"


class MasterLedgerEntry(models.Model):
    wallet = models.ForeignKey(
        MasterWallet, on_delete=models.CASCADE, related_name="ledger_entries", verbose_name="Кошелёк"
    )
    entry_type = models.CharField(max_length=32, choices=LedgerEntryType.choices, verbose_name="Тип операции")
    amount_uzs = models.IntegerField(verbose_name="Сумма, UZS")
    balance_after_uzs = models.IntegerField(verbose_name="Баланс после")
    note = models.TextField(blank=True, verbose_name="Заметка")
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_ledger_entries",
        verbose_name="Кем создана",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создана")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Операция кошелька"
        verbose_name_plural = "Операции кошельков"

    def __str__(self) -> str:
        return f"{self.wallet} / {self.entry_type} / {self.amount_uzs}"

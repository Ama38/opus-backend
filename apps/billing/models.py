from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.masters.models import MasterProfile


class Package(models.Model):
    """Admin-configurable subscription package: a bundle of order slots."""

    slug = models.SlugField(unique=True)
    name_ru = models.CharField(max_length=120)
    name_uz = models.CharField(max_length=120)
    orders_count = models.PositiveIntegerField()
    price_uzs = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ["sort_order", "orders_count"]

    def __str__(self) -> str:
        return f"{self.name_ru} ({self.orders_count})"


class MasterSubscription(models.Model):
    """The master's current order allowance. One per master.

    Replaces the old money wallet as the gate for going online / receiving
    orders: a master can work while ``orders_remaining > 0`` and the package has
    not expired (and is not frozen).
    """

    master = models.OneToOneField(MasterProfile, on_delete=models.CASCADE, related_name="subscription")
    orders_remaining = models.PositiveIntegerField(default=0)
    activated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_frozen = models.BooleanField(default=False)
    frozen_at = models.DateTimeField(null=True, blank=True)
    # Last expiry/quota reminder already delivered (e.g. "d7", "o5") — prevents
    # the daily reminder job from re-sending the same nudge (TZ §2.5).
    reminder_marker = models.CharField(max_length=8, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
    PENDING = "pending", "Pending"
    ACTIVATED = "activated", "Activated"
    REJECTED = "rejected", "Rejected"


class PackagePurchase(models.Model):
    """A request to buy/activate a package. Payment happens offline; an operator
    activates it in admin, which applies the orders to the subscription."""

    master = models.ForeignKey(MasterProfile, on_delete=models.CASCADE, related_name="package_purchases")
    package = models.ForeignKey(Package, on_delete=models.PROTECT, related_name="purchases", null=True, blank=True)
    orders_count = models.PositiveIntegerField()
    price_uzs = models.PositiveIntegerField(default=0)
    is_free = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=PackagePurchaseStatus.choices, default=PackagePurchaseStatus.PENDING)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activated_package_purchases",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.master} / {self.orders_count} / {self.status}"


class LedgerEntryType(models.TextChoices):
    MANUAL_TOP_UP = "manual_top_up", "Manual top-up"
    PACKAGE_PURCHASE = "package_purchase", "Package purchase"
    ORDER_DEBIT = "order_debit", "Order debit"
    ADJUSTMENT = "adjustment", "Adjustment"


class MasterWallet(models.Model):
    master = models.OneToOneField(MasterProfile, on_delete=models.CASCADE, related_name="wallet")
    balance_uzs = models.PositiveIntegerField(default=0)
    package_orders_remaining = models.PositiveIntegerField(default=0)
    free_orders_remaining = models.PositiveIntegerField(default=10)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.master}: {self.balance_uzs} UZS"


class MasterLedgerEntry(models.Model):
    wallet = models.ForeignKey(MasterWallet, on_delete=models.CASCADE, related_name="ledger_entries")
    entry_type = models.CharField(max_length=32, choices=LedgerEntryType.choices)
    amount_uzs = models.IntegerField()
    balance_after_uzs = models.IntegerField()
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_ledger_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.wallet} / {self.entry_type} / {self.amount_uzs}"

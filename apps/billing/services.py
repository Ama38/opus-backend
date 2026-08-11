from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import (
    LedgerEntryType,
    MasterLedgerEntry,
    MasterSubscription,
    MasterWallet,
    Package,
    PackagePurchase,
    PackagePurchaseStatus,
)


def get_or_create_subscription(master) -> MasterSubscription:
    subscription, _ = MasterSubscription.objects.get_or_create(master=master)
    return subscription


def package_expiry_days() -> int:
    return int(getattr(settings, "MASTERGO_PACKAGE_EXPIRY_DAYS", 30))


def free_packages_enabled() -> bool:
    return bool(getattr(settings, "MASTERGO_FREE_PACKAGES", True))


@transaction.atomic
def activate_package(
    master,
    *,
    orders_count: int,
    days: int | None = None,
    activated_by=None,
) -> MasterSubscription:
    """Apply an order allowance to the master's subscription.

    Remaining orders are summed; the expiry is extended by ``days`` from the
    later of now or the current expiry, so a master who buys early never loses
    days or orders. Unfreezes the subscription.
    """
    subscription = MasterSubscription.objects.select_for_update().get_or_create(master=master)[0]
    days = days if days is not None else package_expiry_days()

    base = subscription.expires_at
    now = timezone.now()
    if base is None or base <= now or subscription.orders_remaining == 0:
        base = now
    subscription.orders_remaining += orders_count
    subscription.expires_at = base + timedelta(days=days)
    subscription.activated_at = now
    subscription.is_frozen = False
    subscription.frozen_at = None
    subscription.save()
    return subscription


@transaction.atomic
def request_package(master, package: Package, *, actor=None) -> PackagePurchase:
    """Create a purchase request. During the free launch period it is activated
    immediately; otherwise it stays pending for an operator to confirm payment.
    """
    is_free = free_packages_enabled()
    purchase = PackagePurchase.objects.create(
        master=master,
        package=package,
        orders_count=package.orders_count,
        price_uzs=0 if is_free else package.price_uzs,
        is_free=is_free,
        status=PackagePurchaseStatus.PENDING,
    )
    if is_free:
        activate_purchase(purchase, activated_by=actor)
    return purchase


@transaction.atomic
def activate_purchase(purchase: PackagePurchase, *, activated_by=None) -> PackagePurchase:
    if purchase.status == PackagePurchaseStatus.ACTIVATED:
        return purchase
    activate_package(
        purchase.master,
        orders_count=purchase.orders_count,
        activated_by=activated_by,
    )
    purchase.status = PackagePurchaseStatus.ACTIVATED
    purchase.activated_at = timezone.now()
    purchase.activated_by = activated_by
    purchase.save(update_fields=["status", "activated_at", "activated_by"])
    return purchase


@transaction.atomic
def consume_order(master, *, note: str = "") -> MasterSubscription:
    """Debit one order slot when the master accepts an order. No refunds."""
    subscription = MasterSubscription.objects.select_for_update().get_or_create(master=master)[0]
    if subscription.orders_remaining > 0:
        subscription.orders_remaining -= 1
        subscription.save(update_fields=["orders_remaining", "updated_at"])
    return subscription


def freeze_subscription(master) -> MasterSubscription:
    subscription = get_or_create_subscription(master)
    if not subscription.is_frozen and subscription.expires_at is not None:
        subscription.is_frozen = True
        subscription.frozen_at = timezone.now()
        subscription.save(update_fields=["is_frozen", "frozen_at", "updated_at"])
    return subscription


def unfreeze_subscription(master) -> MasterSubscription:
    """Resume the countdown: push the expiry forward by the frozen duration so
    frozen days do not count against the master."""
    subscription = get_or_create_subscription(master)
    if subscription.is_frozen:
        if subscription.frozen_at and subscription.expires_at:
            frozen_delta = timezone.now() - subscription.frozen_at
            subscription.expires_at = subscription.expires_at + frozen_delta
        subscription.is_frozen = False
        subscription.frozen_at = None
        subscription.save(update_fields=["is_frozen", "frozen_at", "expires_at", "updated_at"])
    return subscription


# --- Legacy money wallet (deprecated: kept only so historical data/migrations
#     stay intact; no longer used to gate going online). ---


@transaction.atomic
def top_up_wallet(wallet: MasterWallet, amount_uzs: int, note: str = "", created_by=None) -> MasterWallet:
    if amount_uzs <= 0:
        raise ValueError("Top-up amount must be positive")
    wallet.balance_uzs += amount_uzs
    wallet.save(update_fields=["balance_uzs", "updated_at"])
    MasterLedgerEntry.objects.create(
        wallet=wallet,
        entry_type=LedgerEntryType.MANUAL_TOP_UP,
        amount_uzs=amount_uzs,
        balance_after_uzs=wallet.balance_uzs,
        note=note,
        created_by=created_by,
    )
    return wallet

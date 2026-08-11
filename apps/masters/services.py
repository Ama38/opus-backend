from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.billing.models import MasterSubscription, MasterWallet
from apps.orders.models import MasterOffer, MasterOfferStatus, Order, OrderStatus

from .models import MasterProfile, MasterStatus


@dataclass(frozen=True)
class MasterScheduleItem:
    order_id: str
    category: str
    scheduled_at: datetime | None
    status: str
    amount_uzs: int
    address: str


@dataclass(frozen=True)
class MasterAnalytics:
    earned_today_uzs: int
    earned_yesterday_uzs: int
    orders_today: int
    acceptance_rate_percent: int
    rating_avg: Decimal
    total_orders: int
    schedule_today: list[MasterScheduleItem]


def get_or_create_master_profile(user) -> MasterProfile:
    profile, created = MasterProfile.objects.get_or_create(user=user)
    MasterWallet.objects.get_or_create(master=profile)
    MasterSubscription.objects.get_or_create(master=profile)
    if created and getattr(settings, "MASTERGO_AUTO_APPROVE_MASTERS", False):
        auto_approve_master(profile)
    return profile


def auto_approve_master(profile: MasterProfile) -> MasterProfile:
    """TEST MODE: approve the master and grant a test package so they can go
    online immediately, bypassing operator moderation. Idempotent."""
    from apps.billing.services import activate_package, get_or_create_subscription

    if profile.status != MasterStatus.APPROVED:
        profile.approve()
    subscription = get_or_create_subscription(profile)
    if not subscription.is_active:
        activate_package(profile, orders_count=100, days=90)
    return profile


def master_has_active_subscription(master: MasterProfile) -> bool:
    subscription = MasterSubscription.objects.filter(master=master).first()
    return subscription is not None and subscription.is_active


def master_can_receive_orders(master: MasterProfile) -> bool:
    if master.status != MasterStatus.APPROVED:
        return False
    if not master.is_online:
        return False
    if not master_has_active_subscription(master):
        return False
    active_statuses = [
        OrderStatus.OFFERED_TO_MASTER,
        OrderStatus.ACCEPTED_BY_MASTER,
        OrderStatus.PRICE_PROPOSED,
        OrderStatus.PRICE_ACCEPTED,
        OrderStatus.MASTER_ON_WAY,
        OrderStatus.MASTER_ARRIVED,
        OrderStatus.IN_PROGRESS,
        OrderStatus.WORK_DONE,
        OrderStatus.DISPUTED,
    ]
    return not master.orders.filter(status__in=active_statuses).exists()


def get_master_analytics(master: MasterProfile, *, today: date | None = None) -> MasterAnalytics:
    today = today or timezone.localdate()
    yesterday = today - timedelta(days=1)
    today_start, today_end = _day_bounds(today)
    yesterday_start, yesterday_end = _day_bounds(yesterday)

    completed_orders = Order.objects.filter(master=master, status=OrderStatus.COMPLETED)
    earned_today = _sum_order_amounts(
        completed_orders.filter(completed_at__gte=today_start, completed_at__lt=today_end)
    )
    earned_yesterday = _sum_order_amounts(
        completed_orders.filter(completed_at__gte=yesterday_start, completed_at__lt=yesterday_end)
    )

    today_orders = (
        Order.objects.filter(master=master)
        .filter(Q(scheduled_at__date=today) | Q(scheduled_at__isnull=True, created_at__gte=today_start, created_at__lt=today_end))
        .select_related("category")
        .order_by("scheduled_at", "created_at")
    )

    return MasterAnalytics(
        earned_today_uzs=earned_today,
        earned_yesterday_uzs=earned_yesterday,
        orders_today=today_orders.count(),
        acceptance_rate_percent=_acceptance_rate_percent(master),
        rating_avg=master.rating or Decimal("0"),
        total_orders=completed_orders.count(),
        schedule_today=[_schedule_item(order) for order in today_orders[:20]],
    )


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    current = timezone.make_aware(datetime.combine(day, time.min), timezone.get_current_timezone())
    return current, current + timedelta(days=1)


def _sum_order_amounts(orders) -> int:
    total = 0
    for order in orders.only("final_price_uzs", "agreed_price_uzs"):
        total += order.final_price_uzs or order.agreed_price_uzs or 0
    return total


def _acceptance_rate_percent(master: MasterProfile) -> int:
    decided = MasterOffer.objects.filter(
        master=master,
        status__in=[MasterOfferStatus.ACCEPTED, MasterOfferStatus.DECLINED],
    )
    total = decided.count()
    if total == 0:
        return 0
    accepted = decided.filter(status=MasterOfferStatus.ACCEPTED).count()
    return round(accepted / total * 100)


def _schedule_item(order: Order) -> MasterScheduleItem:
    return MasterScheduleItem(
        order_id=str(order.id),
        category=order.category.slug,
        scheduled_at=order.scheduled_at,
        status=order.status,
        amount_uzs=order.final_price_uzs or order.agreed_price_uzs or 0,
        address=order.address_text,
    )

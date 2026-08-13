from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from math import asin, cos, radians, sin, sqrt

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.billing.services import consume_order
from apps.masters.models import MasterProfile
from apps.masters.services import master_can_receive_orders, master_has_active_subscription
from apps.notifications.models import NotificationEvent
from apps.notifications.realtime import safe_group_send
from apps.notifications.services import create_in_app_notification

from .models import (
    MasterOffer,
    MasterOfferStatus,
    Order,
    OrderCancelReason,
    OrderEvent,
    OrderStatus,
    PriceProposal,
    PriceProposalStatus,
)

# Radius stages (km), configurable for deployments but aligned with the PRD by
# default: nearby first, then a measured expansion without leaking city-wide
# stale offers to distant masters.
MATCHING_RADII_KM = tuple(getattr(settings, "MASTERGO_MATCHING_RADII_KM", (1, 3, 6)))
MASTER_OFFER_TTL_SECONDS = int(getattr(settings, "MASTERGO_OFFER_TTL_SECONDS", 60))
SEARCH_ORDER_TTL_MINUTES = int(
    getattr(settings, "MASTERGO_SEARCH_ORDER_TTL_MINUTES", 30)
)
_DISTANCE_SCORE_CAP_KM = 10.0


class OrderActionError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    OrderStatus.DRAFT: {OrderStatus.SEARCHING, OrderStatus.CANCELLED},
    OrderStatus.SEARCHING: {OrderStatus.OFFERED_TO_MASTER, OrderStatus.CANCELLED, OrderStatus.EXPIRED},
    OrderStatus.OFFERED_TO_MASTER: {
        OrderStatus.ACCEPTED_BY_MASTER,
        OrderStatus.SEARCHING,
        OrderStatus.CANCELLED,
        OrderStatus.EXPIRED,
    },
    OrderStatus.ACCEPTED_BY_MASTER: {OrderStatus.PRICE_PROPOSED, OrderStatus.SEARCHING, OrderStatus.CANCELLED},
    OrderStatus.PRICE_PROPOSED: {
        OrderStatus.ACCEPTED_BY_MASTER,
        OrderStatus.PRICE_ACCEPTED,
        OrderStatus.SEARCHING,
        OrderStatus.CANCELLED,
    },
    OrderStatus.PRICE_ACCEPTED: {OrderStatus.MASTER_ON_WAY, OrderStatus.CANCELLED, OrderStatus.DISPUTED},
    OrderStatus.MASTER_ON_WAY: {OrderStatus.MASTER_ARRIVED, OrderStatus.CANCELLED, OrderStatus.DISPUTED},
    OrderStatus.MASTER_ARRIVED: {OrderStatus.IN_PROGRESS, OrderStatus.CANCELLED, OrderStatus.DISPUTED},
    OrderStatus.IN_PROGRESS: {OrderStatus.WORK_DONE, OrderStatus.DISPUTED},
    OrderStatus.WORK_DONE: {OrderStatus.COMPLETED, OrderStatus.DISPUTED},
    OrderStatus.DISPUTED: {OrderStatus.COMPLETED, OrderStatus.CANCELLED},
}


def transition_order(order: Order, to_status: str, *, actor=None, reason: str = "", metadata: dict | None = None) -> Order:
    allowed = ALLOWED_TRANSITIONS.get(order.status, set())
    if to_status not in allowed and order.status != to_status:
        raise ValueError(f"Invalid order transition: {order.status} -> {to_status}")

    from_status = order.status
    order.status = to_status
    if to_status == OrderStatus.COMPLETED:
        order.completed_at = timezone.now()
        order.save(update_fields=["status", "completed_at", "updated_at"])
    else:
        order.save(update_fields=["status", "updated_at"])

    event = OrderEvent.objects.create(
        order=order,
        actor=actor,
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        metadata=metadata or {},
    )
    _broadcast_order_event(
        order,
        {
            "event": "order.status_changed",
            "order_id": str(order.id),
            "event_id": event.id,
            "from_status": from_status,
            "to_status": to_status,
            "status": order.status,
            "reason": reason,
            "metadata": metadata or {},
        },
        include_master_channel=to_status == OrderStatus.OFFERED_TO_MASTER,
    )
    if to_status in {OrderStatus.COMPLETED, OrderStatus.CANCELLED, OrderStatus.EXPIRED}:
        from apps.chat.services import close_order_room

        close_order_room(order, reason=reason)
    create_order_notifications(order, from_status, to_status, reason)
    _broadcast_master_analytics_updated(order)
    return order


def _broadcast_order_event(order: Order, payload: dict, *, include_master_channel: bool = False) -> None:
    from .serializers import OrderSerializer

    order_snapshot = Order.objects.select_related("client", "master__user", "category").get(id=order.id)
    payload = {**payload, "order": OrderSerializer(order_snapshot).data}
    safe_group_send(
        f"order_{order.id}",
        {"type": "order.event", "payload": payload},
    )
    if include_master_channel and order_snapshot.master_id:
        safe_group_send(
            f"master_user_{order_snapshot.master.user_id}",
            {"type": "order.event", "payload": payload},
        )


def _master_group_name(master: MasterProfile) -> str:
    return f"master_user_{master.user_id}"


def _master_offer_payload(offer: MasterOffer, event: str) -> dict:
    from .serializers import OrderSerializer

    offer_snapshot = (
        MasterOffer.objects.select_related("order__client", "order__master__user", "order__category", "master__user")
        .prefetch_related("order__attachments", "order__price_proposals")
        .get(id=offer.id)
    )
    now = timezone.now()
    return {
        "event": event,
        "type": event,
        "offer_id": offer_snapshot.id,
        "order_id": str(offer_snapshot.order_id),
        "status": offer_snapshot.status,
        "expires_at": offer_snapshot.expires_at.isoformat(),
        "ttl_seconds": max(0, int((offer_snapshot.expires_at - now).total_seconds())),
        "order": OrderSerializer(offer_snapshot.order).data,
    }


def _broadcast_master_offer_event(offer: MasterOffer, event: str) -> None:
    safe_group_send(
        _master_group_name(offer.master),
        {"type": "order.event", "payload": _master_offer_payload(offer, event)},
    )


def _broadcast_master_analytics_updated(order: Order) -> None:
    if not order.master_id:
        return
    order_snapshot = Order.objects.select_related("master__user").get(id=order.id)
    if not order_snapshot.master_id:
        return
    safe_group_send(
        _master_group_name(order_snapshot.master),
        {
            "type": "order.event",
            "payload": {
                "event": "analytics_updated",
                "type": "analytics_updated",
                "order_id": str(order_snapshot.id),
                "master_id": order_snapshot.master_id,
                "status": order_snapshot.status,
            },
        },
    )


def create_order_notifications(order: Order, from_status: str, to_status: str, reason: str = "") -> None:
    payload = {
        "order_id": str(order.id),
        "from_status": from_status,
        "to_status": to_status,
    }

    if to_status == OrderStatus.OFFERED_TO_MASTER and order.master_id:
        _notify(
            order.master.user,
            "order.offered",
            "Новая заявка рядом",
            "Yaqinda yangi buyurtma bor",
            order.address_text,
            order.address_text,
            payload,
        )
        _push_incoming_offer(order)
        return

    if to_status == OrderStatus.SEARCHING and from_status == OrderStatus.OFFERED_TO_MASTER:
        _notify(
            order.client,
            "order.searching",
            "Ищем другого мастера",
            "Boshqa ustani qidiryapmiz",
            "Заявка снова в поиске.",
            "Buyurtma yana qidiruvda.",
            payload,
        )
        return

    if (
        to_status == OrderStatus.ACCEPTED_BY_MASTER
        and reason == "price_bargain_requested"
        and order.master_id
    ):
        _notify(
            order.master.user,
            "order.price_bargain_requested",
            "Клиент хочет обсудить цену",
            "Mijoz narxni muhokama qilmoqchi",
            "Откройте чат и предложите новую фиксированную цену.",
            "Chatni oching va yangi qatʼiy narx taklif qiling.",
            payload,
        )
        _notify(
            order.client,
            "order.price_bargain_requested",
            "Торг продолжен",
            "Savdolashish davom etmoqda",
            "Мастер получил запрос и может предложить новую цену.",
            "Usta soʻrovni oldi va yangi narx taklif qilishi mumkin.",
            payload,
        )
        _push_client_update(
            order,
            title_ru="Торг",
            title_uz="Savdolashuv",
            body_ru="Мы попросили мастера предложить новую цену.",
            body_uz="Ustadan yangi narx taklif qilishini so‘radik.",
            event="order.price_bargain_requested",
        )
        return

    if to_status == OrderStatus.ACCEPTED_BY_MASTER and order.master_id:
        master_name = str(order.master.user)
        _notify(
            order.client,
            "order.master_accepted",
            "Мастер принял заказ",
            "Usta buyurtmani qabul qildi",
            master_name,
            master_name,
            payload,
        )
        _notify(
            order.master.user,
            "order.accepted",
            "Вы приняли заказ",
            "Buyurtmani qabul qildingiz",
            order.address_text,
            order.address_text,
            payload,
        )
        return

    if to_status == OrderStatus.PRICE_PROPOSED and order.master_id:
        _notify(
            order.client,
            "order.price_proposed",
            "Мастер предложил цену",
            "Usta narx taklif qildi",
            "Проверьте предложение в заказе.",
            "Buyurtmada taklifni tekshiring.",
            payload,
        )
        return

    if to_status == OrderStatus.PRICE_ACCEPTED and order.master_id:
        _notify(
            order.master.user,
            "order.price_accepted",
            "Клиент принял цену",
            "Mijoz narxni qabul qildi",
            "Можно выезжать к клиенту.",
            "Mijozga borishingiz mumkin.",
            payload,
        )
        return

    if to_status == OrderStatus.MASTER_ON_WAY:
        _notify(
            order.client,
            "order.master_on_way",
            "Мастер выехал",
            "Usta yo‘lga chiqdi",
            "Следите за статусом заказа.",
            "Buyurtma holatini kuzating.",
            payload,
        )
        return

    if to_status == OrderStatus.MASTER_ARRIVED:
        _notify(
            order.client,
            "order.master_arrived",
            "Мастер на месте",
            "Usta yetib keldi",
            order.address_text,
            order.address_text,
            payload,
        )
        return

    if to_status == OrderStatus.IN_PROGRESS:
        _notify(
            order.client,
            "order.in_progress",
            "Работа началась",
            "Ish boshlandi",
            "Мастер выполняет заказ.",
            "Usta buyurtmani bajaryapti.",
            payload,
        )
        return

    if to_status == OrderStatus.WORK_DONE:
        _notify(
            order.client,
            "order.work_done",
            "Работа выполнена",
            "Ish bajarildi",
            "Проверьте результат и завершите заказ.",
            "Natijani tekshiring va buyurtmani yakunlang.",
            payload,
        )
        # Tray push so the client comes back to confirm + rate even if the app
        # was backgrounded/killed (otherwise the review prompt is missed).
        _push_client_update(
            order,
            title_ru="Работа выполнена",
            title_uz="Ish bajarildi",
            body_ru="Подтвердите завершение и оцените мастера.",
            body_uz="Yakunlashni tasdiqlang va ustani baholang.",
            event="order.work_done",
        )
        return

    if to_status == OrderStatus.COMPLETED:
        if order.master_id:
            _notify(
                order.master.user,
                "order.completed",
                "Заказ завершен",
                "Buyurtma yakunlandi",
                "Оплата и баланс обновлены.",
                "To‘lov va balans yangilandi.",
                payload,
            )
        _notify(
            order.client,
            "order.completed",
            "Заказ завершен",
            "Buyurtma yakunlandi",
            "Спасибо за отзыв.",
            "Fikr uchun rahmat.",
            payload,
        )
        return

    if to_status == OrderStatus.CANCELLED:
        if order.master_id:
            _notify(
                order.master.user,
                "order.cancelled",
                "Заказ отменен",
                "Buyurtma bekor qilindi",
                "Клиент отменил заявку.",
                "Mijoz buyurtmani bekor qildi.",
                payload,
            )
        _notify(
            order.client,
            "order.cancelled",
            "Заказ отменен",
            "Buyurtma bekor qilindi",
            "Если нужно, создайте новую заявку.",
            "Kerak bo‘lsa, yangi buyurtma yarating.",
            payload,
        )
        return

    if to_status == OrderStatus.EXPIRED and reason == OrderCancelReason.NO_MASTER_FOUND:
        _notify(
            order.client,
            "order.no_master_found",
            "Мастер не найден",
            "Usta topilmadi",
            "Попробуйте изменить адрес или создать новую заявку.",
            "Manzilni o'zgartirib yoki yangi buyurtma yaratib ko'ring.",
            payload,
        )
        return


def _notify(user, event_type: str, title_ru: str, title_uz: str, body_ru: str, body_uz: str, payload: dict) -> None:
    is_uzbek = getattr(user, "language", "ru") == "uz"
    create_in_app_notification(
        user=user,
        event_type=event_type,
        title=title_uz if is_uzbek else title_ru,
        body=body_uz if is_uzbek else body_ru,
        payload=payload,
    )


def _push_incoming_offer(order: Order) -> None:
    """Send a high-priority FCM push so the master gets a ringing, full-screen
    incoming-order alert even when the app is backgrounded or the phone locked."""
    from apps.notifications.push import enqueue_push_to_user

    master_user = order.master.user
    is_uzbek = getattr(master_user, "language", "ru") == "uz"
    category = order.category.name_uz if is_uzbek else order.category.name_ru
    title = "Yangi buyurtma" if is_uzbek else "Новая заявка"
    body = f"{category} · {order.address_text}"
    try:
        enqueue_push_to_user(
            master_user,
            title=title,
            body=body,
            data={
                "event": "order.offered",
                "type": "order.offered",
                "order_id": str(order.id),
                "category": category,
                "address": order.address_text,
            },
            channel_id="incoming_orders",
            sound="incoming_call",
            # Android handles this data payload in a native messaging service
            # so it can attach a full-screen intent. An FCM notification block
            # would be consumed by SystemUI before that service can run.
            include_notification=False,
        )
    except Exception:  # pragma: no cover - push must never break the order flow
        pass


def _push_client_update(
    order: Order,
    *,
    title_ru: str,
    title_uz: str,
    body_ru: str,
    body_uz: str,
    event: str,
) -> None:
    """Send a normal tray push to the client for an order status update. Unlike
    the master's ringing incoming-order alert, this shows up in the tray via the
    notification block so the client is reliably pulled back into the flow even
    when the app is backgrounded or killed."""
    from apps.notifications.push import enqueue_push_to_user

    client = order.client
    is_uzbek = getattr(client, "language", "ru") == "uz"
    try:
        enqueue_push_to_user(
            client,
            title=title_uz if is_uzbek else title_ru,
            body=body_uz if is_uzbek else body_ru,
            data={
                "event": event,
                "type": event,
                "order_id": str(order.id),
            },
            channel_id="order_updates",
            sound="default",
            include_notification=True,
        )
    except Exception:  # pragma: no cover - push must never break the order flow
        pass


def haversine_km(lat1: Decimal, lon1: Decimal, lat2: Decimal, lon2: Decimal) -> float:
    radius = 6371.0
    d_lat = radians(float(lat2 - lat1))
    d_lon = radians(float(lon2 - lon1))
    a = sin(d_lat / 2) ** 2 + cos(radians(float(lat1))) * cos(radians(float(lat2))) * sin(d_lon / 2) ** 2
    return 2 * radius * asin(sqrt(a))


@dataclass(frozen=True)
class MasterScore:
    master: MasterProfile
    distance_km: float
    score: float


@dataclass(frozen=True)
class MatchAttemptResult:
    order: Order
    offer: MasterOffer | None
    attempted_radii_km: tuple[int, ...]
    exhausted: bool = False


def _effective_rating(master: MasterProfile) -> float:
    """New masters (<10 completed) get the platform starter rating instead of 0,
    so they can compete while building their own history (cold-start fix)."""
    threshold = int(getattr(settings, "MASTERGO_NEWCOMER_ORDER_THRESHOLD", 10))
    starter = float(getattr(settings, "MASTERGO_STARTER_RATING", 4.5))
    if master.completed_orders_count < threshold:
        return max(float(master.rating or 0), starter)
    return float(master.rating or 0)


def _completion_rate(master: MasterProfile) -> float:
    accepted = master.order_offers.filter(status=MasterOfferStatus.ACCEPTED).count()
    if accepted == 0:
        return 1.0  # no data yet: don't penalise
    return min(1.0, master.completed_orders_count / accepted)


def _reaction_score(master: MasterProfile) -> float:
    """1.0 = instant reactions, 0.0 = always uses the full TTL. Newcomers with no
    responses get the benefit of the doubt (1.0). Computed in Python so it works
    the same on Postgres and SQLite (tests)."""
    pairs = master.order_offers.filter(
        status__in=[MasterOfferStatus.ACCEPTED, MasterOfferStatus.DECLINED],
        responded_at__isnull=False,
    ).values_list("created_at", "responded_at")
    seconds = [(responded - created).total_seconds() for created, responded in pairs]
    if not seconds:
        return 1.0
    avg = sum(seconds) / len(seconds)
    return max(0.0, 1.0 - min(avg, MASTER_OFFER_TTL_SECONDS) / MASTER_OFFER_TTL_SECONDS)


def score_master(order: Order, master: MasterProfile, distance_km: float) -> MasterScore:
    distance_score = max(0.0, 1.0 - min(distance_km, _DISTANCE_SCORE_CAP_KM) / _DISTANCE_SCORE_CAP_KM)
    rating_score = _effective_rating(master) / 5.0
    completion_score = _completion_rate(master)
    reaction_score = _reaction_score(master)
    weights = _match_weights()
    score = (
        distance_score * weights[0]
        + rating_score * weights[1]
        + completion_score * weights[2]
        + reaction_score * weights[3]
    )
    return MasterScore(master=master, distance_km=distance_km, score=score)


def _match_weights() -> tuple[float, float, float, float]:
    """Operator-editable matching weights (DB singleton), falling back to
    Django settings if the platform_config table is unavailable."""
    try:
        from apps.platform_config.models import PlatformSettings

        cfg = PlatformSettings.load()
        return (
            cfg.match_weight_distance,
            cfg.match_weight_rating,
            cfg.match_weight_completion,
            cfg.match_weight_reaction,
        )
    except Exception:  # noqa: BLE001 - never let config lookup break matching
        return (
            float(getattr(settings, "MASTERGO_MATCH_WEIGHT_DISTANCE", 0.50)),
            float(getattr(settings, "MASTERGO_MATCH_WEIGHT_RATING", 0.30)),
            float(getattr(settings, "MASTERGO_MATCH_WEIGHT_COMPLETION", 0.10)),
            float(getattr(settings, "MASTERGO_MATCH_WEIGHT_REACTION", 0.10)),
        )


def find_candidate_masters(order: Order, radius_km: int) -> list[MasterScore]:
    if order.latitude is None or order.longitude is None:
        return []

    masters = (
        MasterProfile.objects.filter(
            category_prices__category=order.category,
            category_prices__is_active=True,
            category_prices__status="approved",
            current_latitude__isnull=False,
            current_longitude__isnull=False,
        )
        .select_related("user")
        .distinct()
    )

    scored: list[MasterScore] = []
    offered_master_ids = set(order.master_offers.values_list("master_id", flat=True))
    for master in masters:
        if master.id in offered_master_ids:
            continue
        if not master_can_receive_orders(master):
            continue
        distance_km = haversine_km(order.latitude, order.longitude, master.current_latitude, master.current_longitude)
        # radius_km == 0 means "whole city": no distance cap.
        if radius_km == 0 or distance_km <= radius_km:
            scored.append(score_master(order, master, distance_km))

    return sorted(scored, key=lambda item: item.score, reverse=True)


def _pick_best_candidate(candidates: list[MasterScore]) -> MasterScore:
    """Usually the top-scored master, but every Nth platform offer prioritises a
    newcomer (<10 completed orders) if one is in range, so new masters get real
    orders instead of watching their package expire."""
    threshold = int(getattr(settings, "MASTERGO_NEWCOMER_ORDER_THRESHOLD", 10))
    every = int(getattr(settings, "MASTERGO_NEWCOMER_PRIORITY_EVERY", 5))
    if every > 0 and (MasterOffer.objects.count() % every == 0):
        newcomers = [c for c in candidates if c.master.completed_orders_count < threshold]
        if newcomers:
            return newcomers[0]
    return candidates[0]


def expire_stale_master_offers(
    *,
    continue_matching: bool = False,
    master: MasterProfile | None = None,
    order: Order | None = None,
    limit: int = 100,
) -> int:
    queryset = MasterOffer.objects.filter(
        status=MasterOfferStatus.PENDING,
        expires_at__lte=timezone.now(),
        order__status=OrderStatus.OFFERED_TO_MASTER,
    )
    if master is not None:
        queryset = queryset.filter(master=master)
    if order is not None:
        queryset = queryset.filter(order=order)
    offer_ids = list(
        queryset.order_by("expires_at").values_list("id", flat=True)[:limit]
    )
    expired_count = 0
    for offer_id in offer_ids:
        if expire_master_offer(offer_id, continue_matching=continue_matching):
            expired_count += 1
    return expired_count


def expire_stale_searching_orders(
    *,
    client=None,
    order: Order | None = None,
    limit: int = 100,
) -> int:
    """Expire urgent requests that were never accepted within the search TTL."""
    cutoff = timezone.now() - timedelta(minutes=SEARCH_ORDER_TTL_MINUTES)
    queryset = Order.objects.filter(
        status__in=[OrderStatus.SEARCHING, OrderStatus.OFFERED_TO_MASTER],
        created_at__lte=cutoff,
    )
    if client is not None:
        queryset = queryset.filter(client=client)
    if order is not None:
        queryset = queryset.filter(id=order.id)
    order_ids = list(
        queryset.order_by("created_at").values_list("id", flat=True)[:limit]
    )
    expired_count = 0
    for order_id in order_ids:
        expired_offers: list[MasterOffer] = []
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(id=order_id).first()
            if order is None or order.status not in {
                OrderStatus.SEARCHING,
                OrderStatus.OFFERED_TO_MASTER,
            }:
                continue
            expired_offers = list(
                order.master_offers.select_related("master__user").filter(
                    status=MasterOfferStatus.PENDING
                )
            )
            now = timezone.now()
            for offer in expired_offers:
                offer.status = MasterOfferStatus.EXPIRED
                offer.responded_at = now
                offer.save(update_fields=["status", "responded_at"])
            order.master = None
            order.save(update_fields=["master", "updated_at"])
            transition_order(
                order,
                OrderStatus.EXPIRED,
                reason="search_timeout",
                metadata={"ttl_minutes": SEARCH_ORDER_TTL_MINUTES},
            )
            expired_count += 1
        for offer in expired_offers:
            _broadcast_master_offer_event(offer, "offer_expired")
    return expired_count


def expire_master_offer(offer_id: int, *, continue_matching: bool = True) -> bool:
    order_id = None
    expired_offer = None
    with transaction.atomic():
        offer = (
            MasterOffer.objects.select_for_update()
            .select_related("order", "master__user")
            .filter(id=offer_id)
            .first()
        )
        if offer is None:
            return False
        if offer.status != MasterOfferStatus.PENDING:
            return False
        if offer.expires_at > timezone.now():
            return False

        offer.status = MasterOfferStatus.EXPIRED
        offer.responded_at = timezone.now()
        offer.save(update_fields=["status", "responded_at"])

        order = offer.order
        if order.status == OrderStatus.OFFERED_TO_MASTER and order.master_id == offer.master_id:
            order.master = None
            order.save(update_fields=["master", "updated_at"])
            transition_order(
                order,
                OrderStatus.SEARCHING,
                reason=OrderCancelReason.MASTER_NO_RESPONSE,
                metadata={"offer_id": offer.id},
            )
            order_id = order.id
        expired_offer = offer

    if expired_offer is not None:
        _broadcast_master_offer_event(expired_offer, "offer_expired")
    if continue_matching and order_id is not None:
        order = Order.objects.select_related("client", "category").get(id=order_id)
        match_order_with_radius_expansion(order, start_radius_km=MATCHING_RADII_KM[0])
    return True


def decline_master_offer(offer: MasterOffer, *, actor=None) -> MatchAttemptResult:
    order_id = None
    declined_offer = None
    with transaction.atomic():
        offer = (
            MasterOffer.objects.select_for_update()
            .select_related("order", "master__user")
            .get(id=offer.id)
        )
        order = offer.order
        if offer.status != MasterOfferStatus.PENDING:
            raise OrderActionError("offer_not_pending")
        if offer.expires_at <= timezone.now():
            raise OrderActionError("offer_expired")
        if order.status != OrderStatus.OFFERED_TO_MASTER:
            raise OrderActionError("order_not_accepting_offers")
        if order.master_id != offer.master_id:
            raise OrderActionError("offer_not_current")

        offer.status = MasterOfferStatus.DECLINED
        offer.responded_at = timezone.now()
        offer.save(update_fields=["status", "responded_at"])
        order.master = None
        order.save(update_fields=["master", "updated_at"])
        transition_order(
            order,
            OrderStatus.SEARCHING,
            actor=actor,
            reason=OrderCancelReason.MASTER_DECLINED,
            metadata={"offer_id": offer.id},
        )
        order_id = order.id
        declined_offer = offer

    _broadcast_master_offer_event(declined_offer, "offer_declined")
    order = Order.objects.select_related("client", "category").get(id=order_id)
    return match_order_with_radius_expansion(order, start_radius_km=MATCHING_RADII_KM[0])


def client_reject_master(order: Order, *, actor=None) -> MatchAttemptResult:
    """Client asked for a different master ("искать другого"). The current
    master is dropped (their debited order is NOT refunded) and excluded from
    re-matching. After the configured number of refusals the order is routed to
    an operator instead of auto-matching."""
    reassignable = {
        OrderStatus.OFFERED_TO_MASTER,
        OrderStatus.ACCEPTED_BY_MASTER,
        OrderStatus.PRICE_PROPOSED,
    }
    order_id = None
    dropped_offer = None
    route_to_operator = False
    with transaction.atomic():
        order = Order.objects.select_for_update().select_related("client").get(id=order.id)
        if order.status not in reassignable:
            raise OrderActionError("order_not_reassignable")

        order.client_refusals += 1
        refusals = order.client_refusals
        threshold = int(getattr(settings, "MASTERGO_CLIENT_REFUSALS_TO_OPERATOR", 3))

        current = order.master_offers.filter(status=MasterOfferStatus.PENDING).order_by("-created_at").first()
        if current is not None:
            current.status = MasterOfferStatus.DECLINED
            current.responded_at = timezone.now()
            current.save(update_fields=["status", "responded_at"])
            dropped_offer = current

        # Notify the dropped master (their unit stays spent).
        if order.master_id:
            _notify(
                order.master.user,
                "order.client_left",
                "Клиент ищет другого мастера",
                "Mijoz boshqa usta qidiryapti",
                "Клиент решил искать другого мастера.",
                "Mijoz boshqa usta qidirishga qaror qildi.",
                {"order_id": str(order.id)},
            )

        order.master = None
        route_to_operator = refusals >= threshold
        if route_to_operator:
            order.needs_operator = True
        order.save(update_fields=["master", "client_refusals", "needs_operator", "updated_at"])
        transition_order(
            order,
            OrderStatus.SEARCHING,
            actor=actor,
            reason="client_refused_master",
            metadata={"refusals": refusals, "needs_operator": route_to_operator},
        )
        order_id = order.id

    if dropped_offer is not None:
        _broadcast_master_offer_event(dropped_offer, "offer_declined")

    order = Order.objects.select_related("client", "category").get(id=order_id)
    if route_to_operator:
        _notify(
            order.client,
            "order.operator_search",
            "Подбираем мастера вручную",
            "Ustani qo‘lda tanlayapmiz",
            "Оператор свяжется с вами и подберёт мастера.",
            "Operator siz bilan bog‘lanadi va usta tanlaydi.",
            {"order_id": str(order.id)},
        )
        return MatchAttemptResult(order=order, offer=None, attempted_radii_km=(), exhausted=True)
    return match_order_with_radius_expansion(order, start_radius_km=MATCHING_RADII_KM[0])


@transaction.atomic
def offer_order_to_best_master(order: Order, radius_km: int = MATCHING_RADII_KM[0]) -> MasterOffer | None:
    expire_stale_master_offers()
    order.refresh_from_db(fields=["status", "master", "updated_at"])
    current_offer = order.master_offers.filter(
        status=MasterOfferStatus.PENDING,
        expires_at__gt=timezone.now(),
    ).order_by("-created_at").first()
    if order.status == OrderStatus.OFFERED_TO_MASTER and current_offer and order.master_id == current_offer.master_id:
        return current_offer
    if order.status == OrderStatus.DRAFT:
        transition_order(order, OrderStatus.SEARCHING, reason="matching_started")

    candidates = find_candidate_masters(order, radius_km)
    if not candidates:
        return None

    best = _pick_best_candidate(candidates)
    offer = MasterOffer.objects.create(
        order=order,
        master=best.master,
        score=best.score,
        radius_km=radius_km,
        expires_at=timezone.now() + timedelta(seconds=MASTER_OFFER_TTL_SECONDS),
    )
    order.master = best.master
    order.save(update_fields=["master", "updated_at"])
    transition_order(
        order,
        OrderStatus.OFFERED_TO_MASTER,
        reason="matching_offer_created",
        metadata={"radius_km": radius_km, "score": best.score, "distance_km": best.distance_km},
    )
    _broadcast_master_offer_event(offer, "offer")
    return offer


def _offer_to_preferred_master(order: Order) -> MasterOffer | None:
    """Direct offer to the master the client picked from the directory. Skipped
    if they were already offered this order (so a decline/expire falls through to
    normal matching), or if they can't currently take it."""
    master = order.preferred_master
    if master is None:
        return None
    if order.master_offers.filter(master=master).exists():
        return None
    if not master_can_receive_orders(master):
        return None
    if not master.category_prices.filter(
        category=order.category, is_active=True, status="approved"
    ).exists():
        return None

    offer = MasterOffer.objects.create(
        order=order,
        master=master,
        score=1.0,
        radius_km=0,
        expires_at=timezone.now() + timedelta(seconds=MASTER_OFFER_TTL_SECONDS),
    )
    order.master = master
    order.save(update_fields=["master", "updated_at"])
    transition_order(
        order,
        OrderStatus.OFFERED_TO_MASTER,
        reason="preferred_master_offer",
        metadata={"preferred": True},
    )
    _broadcast_master_offer_event(offer, "offer")
    return offer


@transaction.atomic
def match_order_with_radius_expansion(order: Order, start_radius_km: int = MATCHING_RADII_KM[0]) -> MatchAttemptResult:
    expire_stale_searching_orders(order=order, limit=1)
    expire_stale_master_offers(order=order, limit=1)
    order.refresh_from_db(fields=["status", "master", "updated_at"])
    if order.status == OrderStatus.EXPIRED:
        return MatchAttemptResult(
            order=order,
            offer=None,
            attempted_radii_km=(),
            exhausted=True,
        )
    current_offer = order.master_offers.filter(
        status=MasterOfferStatus.PENDING,
        expires_at__gt=timezone.now(),
    ).order_by("-created_at").first()
    if order.status == OrderStatus.OFFERED_TO_MASTER and current_offer and order.master_id == current_offer.master_id:
        return MatchAttemptResult(
            order=order,
            offer=current_offer,
            attempted_radii_km=(current_offer.radius_km,),
        )
    if start_radius_km not in MATCHING_RADII_KM:
        raise ValueError(f"Unsupported matching radius: {start_radius_km}")

    if order.status == OrderStatus.DRAFT:
        transition_order(order, OrderStatus.SEARCHING, reason="matching_started")

    # Client picked a specific master: give them the first (direct) offer. If
    # they already had an offer (declined/expired), fall through to normal match.
    preferred_offer = _offer_to_preferred_master(order)
    if preferred_offer is not None:
        return MatchAttemptResult(order=order, offer=preferred_offer, attempted_radii_km=(0,))

    start_index = MATCHING_RADII_KM.index(start_radius_km)
    attempted_radii = MATCHING_RADII_KM[start_index:]
    for radius_km in attempted_radii:
        offer = offer_order_to_best_master(order, radius_km=radius_km)
        if offer is not None:
            return MatchAttemptResult(
                order=order,
                offer=offer,
                attempted_radii_km=attempted_radii,
            )

    order.master = None
    order.save(update_fields=["master", "updated_at"])
    if not NotificationEvent.objects.filter(
        user=order.client,
        event_type="order.no_master_found",
        payload__order_id=str(order.id),
    ).exists():
        _notify(
            order.client,
            "order.no_master_found",
            "Мастер не найден",
            "Usta topilmadi",
            "Заявка осталась в поиске. Мы попробуем ещё раз, когда мастер появится рядом.",
            "Buyurtma qidiruvda qoldi. Yaqinda usta paydo bo'lsa, qayta urinib ko'ramiz.",
            {
                "order_id": str(order.id),
                "from_status": order.status,
                "to_status": order.status,
                "attempted_radii_km": list(attempted_radii),
            },
        )
    return MatchAttemptResult(
        order=order,
        offer=None,
        attempted_radii_km=attempted_radii,
        exhausted=True,
    )


def match_open_orders(limit: int = 20, *, reconcile: bool = True) -> int:
    if reconcile:
        expire_stale_searching_orders()
        expire_stale_master_offers()
    matched_count = 0
    orders = (
        Order.objects.filter(status=OrderStatus.SEARCHING, master__isnull=True)
        .select_related("client", "category")
        .order_by("created_at")[:limit]
    )
    for order in orders:
        result = match_order_with_radius_expansion(order, start_radius_km=MATCHING_RADII_KM[0])
        if result.offer is not None:
            matched_count += 1
    return matched_count


@transaction.atomic
def accept_master_offer(offer: MasterOffer) -> Order:
    order = offer.order
    if offer.status != MasterOfferStatus.PENDING:
        raise OrderActionError("offer_not_pending")
    if offer.expires_at <= timezone.now():
        raise OrderActionError("offer_expired")
    if order.status != OrderStatus.OFFERED_TO_MASTER:
        raise OrderActionError("order_not_accepting_offers")
    if order.master_id != offer.master_id:
        raise OrderActionError("offer_not_current")
    if not _master_can_accept_current_offer(offer.master, order):
        raise OrderActionError("master_not_eligible")

    offer.status = MasterOfferStatus.ACCEPTED
    offer.responded_at = timezone.now()
    offer.save(update_fields=["status", "responded_at"])
    order.master = offer.master
    order.save(update_fields=["master", "updated_at"])
    # Debit one order slot from the package at the moment of acceptance (no refunds).
    consume_order(offer.master, note=f"order {order.id}")
    order = transition_order(order, OrderStatus.ACCEPTED_BY_MASTER, actor=offer.master.user, reason="master_accepted")
    _broadcast_master_offer_event(offer, "offer_accepted")
    return order


def _master_can_accept_current_offer(master: MasterProfile, order: Order) -> bool:
    from apps.masters.models import MasterStatus

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
    return not master.orders.filter(status__in=active_statuses).exclude(id=order.id).exists()


@transaction.atomic
def propose_price(order: Order, master: MasterProfile, amount_uzs: int) -> PriceProposal:
    if order.master_id != master.id:
        raise ValueError("Only assigned master can propose price")
    proposal = PriceProposal.objects.create(order=order, master=master, amount_uzs=amount_uzs)
    transition_order(
        order,
        OrderStatus.PRICE_PROPOSED,
        actor=master.user,
        reason="price_proposed",
        metadata={"amount_uzs": amount_uzs},
    )
    return proposal


@transaction.atomic
def accept_price(proposal: PriceProposal, actor) -> Order:
    proposal.status = PriceProposalStatus.ACCEPTED
    proposal.responded_at = timezone.now()
    proposal.save(update_fields=["status", "responded_at"])
    order = proposal.order
    order.agreed_price_uzs = proposal.amount_uzs
    order.save(update_fields=["agreed_price_uzs", "updated_at"])
    transition_order(order, OrderStatus.PRICE_ACCEPTED, actor=actor, reason="price_accepted")
    return transition_order(order, OrderStatus.MASTER_ON_WAY, actor=proposal.master.user, reason="master_departed")


@transaction.atomic
def reject_price(proposal: PriceProposal, actor) -> Order:
    proposal.status = PriceProposalStatus.REJECTED
    proposal.responded_at = timezone.now()
    proposal.save(update_fields=["status", "responded_at"])
    order = proposal.order
    return transition_order(order, OrderStatus.CANCELLED, actor=actor, reason="price_rejected")


@transaction.atomic
def bargain_price(proposal: PriceProposal, actor) -> Order:
    """Decline one quote while keeping the assigned master and chat active."""
    if proposal.status != PriceProposalStatus.PENDING:
        raise OrderActionError("price_proposal_not_pending")
    order = proposal.order
    if order.status != OrderStatus.PRICE_PROPOSED:
        raise OrderActionError("order_not_awaiting_price_response")
    proposal.status = PriceProposalStatus.REJECTED
    proposal.responded_at = timezone.now()
    proposal.save(update_fields=["status", "responded_at"])
    return transition_order(
        order,
        OrderStatus.ACCEPTED_BY_MASTER,
        actor=actor,
        reason="price_bargain_requested",
        metadata={"rejected_amount_uzs": proposal.amount_uzs},
    )

from .services import (
    expire_master_offer,
    expire_stale_master_offers,
    expire_stale_searching_orders,
    match_open_orders,
)


def expire_offer(offer_id: int) -> bool:
    return expire_master_offer(offer_id, continue_matching=True)


def sweep_offer_expirations(limit: int = 20) -> dict[str, int]:
    expired_orders_count = expire_stale_searching_orders()
    expired_count = expire_stale_master_offers(continue_matching=True)
    matched_count = match_open_orders(limit=limit, reconcile=False)
    return {
        "expired_count": expired_count,
        "expired_orders_count": expired_orders_count,
        "matched_count": matched_count,
    }

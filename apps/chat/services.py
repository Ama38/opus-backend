from django.utils import timezone

from apps.notifications.realtime import safe_group_send

from .models import ChatMessage, ChatRoom, MessageKind


def get_or_create_order_room(order) -> ChatRoom:
    room, _ = ChatRoom.objects.get_or_create(order=order)
    return room


def close_order_room(order, *, reason: str = "") -> ChatRoom | None:
    """Close an existing order chat and notify both connected applications.

    A room is deliberately not created just to close it: orders cancelled before
    a master accepts never had a conversation in the first place.
    """
    room = ChatRoom.objects.filter(order=order).first()
    if room is None:
        return None
    if room.closed_at is None:
        room.closed_at = timezone.now()
        room.save(update_fields=["closed_at"])
    safe_group_send(
        f"chat_{room.id}",
        {
            "type": "chat.message",
            "payload": {
                "event": "chat.closed",
                "order_id": str(order.id),
                "order_status": order.status,
                "reason": reason,
                "closed_at": room.closed_at.isoformat(),
            },
        },
    )
    return room


def create_text_message(room: ChatRoom, sender, text: str) -> ChatMessage:
    if room.closed_at is not None:
        raise ValueError("chat_closed")
    if not text.strip():
        raise ValueError("Message text is required")
    return ChatMessage.objects.create(room=room, sender=sender, kind=MessageKind.TEXT, text=text.strip())


def create_price_message(room: ChatRoom, sender, price_uzs: int) -> ChatMessage:
    if room.closed_at is not None:
        raise ValueError("chat_closed")
    if price_uzs <= 0:
        raise ValueError("Price must be positive")
    return ChatMessage.objects.create(
        room=room,
        sender=sender,
        kind=MessageKind.PRICE_PROPOSAL,
        price_uzs=price_uzs,
    )


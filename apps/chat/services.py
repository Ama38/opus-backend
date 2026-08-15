from django.utils import timezone

from apps.notifications.realtime import safe_group_send

from .models import ChatMessage, ChatRoom, MessageKind


def _message_preview(message: ChatMessage, *, is_uzbek: bool) -> str:
    """Short human body for a chat push/in-app notification."""
    if message.kind == MessageKind.PHOTO:
        return "📷 Rasm" if is_uzbek else "📷 Фото"
    if message.kind == MessageKind.VIDEO:
        return "🎬 Video" if is_uzbek else "🎬 Видео"
    if message.kind == MessageKind.PRICE_PROPOSAL:
        return "💰 Narx taklifi" if is_uzbek else "💰 Предложение цены"
    text = (message.text or "").strip()
    if text:
        return text if len(text) <= 140 else f"{text[:139]}…"
    if message.attachment_url:
        return "📎 Ilova" if is_uzbek else "📎 Вложение"
    return "Yangi xabar" if is_uzbek else "Новое сообщение"


def notify_new_chat_message(room: ChatRoom, message: ChatMessage) -> None:
    """Deliver an in-app record + tray push to the chat participant who did not
    send the message (client ↔ master). Best-effort: never breaks the send flow.
    """
    order = room.order
    sender_id = message.sender_id
    client = order.client
    master_user = order.master.user if order.master_id else None

    if sender_id == client.id:
        recipient = master_user
    else:
        recipient = client
    if recipient is None:
        return

    is_uzbek = getattr(recipient, "language", "ru") == "uz"
    sender_name = str(message.sender) or ("Xabar" if is_uzbek else "Сообщение")
    body = _message_preview(message, is_uzbek=is_uzbek)
    data = {
        "event": "chat.message",
        "type": "chat.message",
        "order_id": str(order.id),
        "room_id": str(room.id),
    }

    try:
        from apps.notifications.services import create_in_app_notification

        create_in_app_notification(
            user=recipient,
            event_type="chat.message",
            title=sender_name,
            body=body,
            payload=data,
        )
    except Exception:  # pragma: no cover - in-app log must not break send
        pass

    try:
        from apps.notifications.push import enqueue_push_to_user

        enqueue_push_to_user(
            recipient,
            title=sender_name,
            body=body,
            data=data,
            channel_id="chat_messages",
            sound="default",
            include_notification=True,
        )
    except Exception:  # pragma: no cover - push must never break the send flow
        pass


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


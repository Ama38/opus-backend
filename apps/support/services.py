from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from .models import SupportCase, SupportCaseStatus, SupportMessage


@transaction.atomic
def add_support_message(
    case: SupportCase,
    *,
    sender,
    text: str,
) -> SupportMessage:
    """Append a chat message and keep assignment/status metadata coherent."""
    normalized = text.strip()
    if not normalized:
        raise ValueError("support_message_empty")
    if case.status in {SupportCaseStatus.RESOLVED, SupportCaseStatus.CLOSED}:
        raise ValueError("support_case_closed")

    message = SupportMessage.objects.create(
        case=case,
        sender=sender,
        text=normalized,
    )
    update_fields = ["updated_at"]
    case.updated_at = timezone.now()
    if sender.is_staff:
        case.last_operator_message_at = message.created_at
        update_fields.append("last_operator_message_at")
        if case.assigned_to_id is None:
            case.assigned_to = sender
            update_fields.append("assigned_to")
        if case.status == SupportCaseStatus.OPEN:
            case.status = SupportCaseStatus.IN_PROGRESS
            update_fields.append("status")
    else:
        case.last_user_message_at = message.created_at
        update_fields.append("last_user_message_at")
    case.save(update_fields=update_fields)
    return message


@transaction.atomic
def close_support_case(case: SupportCase, *, reason: str) -> SupportCase:
    if case.status in {SupportCaseStatus.RESOLVED, SupportCaseStatus.CLOSED}:
        return case
    now = timezone.now()
    case.status = SupportCaseStatus.CLOSED
    case.closed_at = now
    case.close_reason = reason
    case.updated_at = now
    case.save(update_fields=["status", "closed_at", "close_reason", "updated_at"])
    return case


def close_inactive_support_cases(*, now=None, limit: int = 100) -> int:
    """Close chats where the operator has waited three hours for the user."""
    from apps.notifications.push import enqueue_push_to_user
    from apps.notifications.services import create_in_app_notification

    now = now or timezone.now()
    hours = int(getattr(settings, "MASTERGO_SUPPORT_INACTIVITY_HOURS", 3))
    cutoff = now - timedelta(hours=hours)
    cases = list(
        SupportCase.objects.filter(
            status__in=[SupportCaseStatus.OPEN, SupportCaseStatus.IN_PROGRESS],
            last_operator_message_at__lte=cutoff,
        )
        .filter(
            models.Q(last_user_message_at__isnull=True)
            | models.Q(last_user_message_at__lte=models.F("last_operator_message_at"))
        )
        .select_related("user")
        .order_by("last_operator_message_at")[:limit]
    )
    closed_count = 0
    for case in cases:
        transitioned = SupportCase.objects.filter(
            id=case.id,
            status__in=[SupportCaseStatus.OPEN, SupportCaseStatus.IN_PROGRESS],
        ).update(
            status=SupportCaseStatus.CLOSED,
            closed_at=now,
            close_reason="user_inactive",
            updated_at=now,
        )
        if not transitioned:
            continue
        closed_count += 1
        is_uz = case.user.language == "uz"
        title = "Murojaat yopildi" if is_uz else "Обращение закрыто"
        in_app_body = (
            "3 soat ichida javob bo‘lmagani uchun yordam chati yopildi."
            if is_uz
            else "Чат поддержки закрыт, потому что ответ не поступил в течение 3 часов."
        )
        push_body = (
            "3 soat ichida javob bo‘lmadi. Zarur bo‘lsa, yangi murojaat yarating."
            if is_uz
            else "Ответ не поступил в течение 3 часов. При необходимости создайте новое обращение."
        )
        create_in_app_notification(
            case.user,
            "support.case_closed",
            title,
            in_app_body,
            {"support_case_id": case.id, "reason": "user_inactive"},
        )
        enqueue_push_to_user(
            case.user,
            title=title,
            body=push_body,
            data={"event": "support.case_closed", "support_case_id": case.id},
            channel_id="order_updates",
            sound="default",
            include_notification=True,
        )
    return closed_count

from django.db import transaction
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

    message = SupportMessage.objects.create(
        case=case,
        sender=sender,
        text=normalized,
    )
    update_fields = ["updated_at"]
    case.updated_at = timezone.now()
    if sender.is_staff:
        if case.assigned_to_id is None:
            case.assigned_to = sender
            update_fields.append("assigned_to")
        if case.status == SupportCaseStatus.OPEN:
            case.status = SupportCaseStatus.IN_PROGRESS
            update_fields.append("status")
    elif case.status in {SupportCaseStatus.RESOLVED, SupportCaseStatus.CLOSED}:
        case.status = SupportCaseStatus.OPEN
        update_fields.append("status")
    case.save(update_fields=update_fields)
    return message

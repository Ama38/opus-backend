from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Avg

from apps.masters.models import MasterProfile

from .models import Review


def recalculate_master_rating(target_user_id: int) -> Decimal | None:
    """Persist the real average of public client reviews for one master."""
    master = MasterProfile.objects.filter(user_id=target_user_id).first()
    if master is None:
        return None

    average = Review.objects.filter(
        target_id=target_user_id,
        is_public=True,
    ).aggregate(value=Avg("rating"))["value"]
    rating = (
        Decimal("0.00")
        if average is None
        else Decimal(str(average)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )
    MasterProfile.objects.filter(id=master.id).update(rating=rating)
    return rating

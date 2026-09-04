from decimal import Decimal, ROUND_HALF_UP

from django.db import migrations
from django.db.models import Avg


def recalculate_existing_ratings(apps, schema_editor):
    MasterProfile = apps.get_model("masters", "MasterProfile")
    Review = apps.get_model("reviews", "Review")
    for master in MasterProfile.objects.all().iterator():
        average = Review.objects.filter(
            target_id=master.user_id,
            is_public=True,
        ).aggregate(value=Avg("rating"))["value"]
        rating = (
            Decimal("0.00")
            if average is None
            else Decimal(str(average)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        )
        MasterProfile.objects.filter(id=master.id).update(rating=rating)


class Migration(migrations.Migration):
    dependencies = [
        ("masters", "0007_masterprofile_online_since"),
        ("reviews", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            recalculate_existing_ratings,
            migrations.RunPython.noop,
        ),
    ]

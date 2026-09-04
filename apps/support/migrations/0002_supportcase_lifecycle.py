from django.db import migrations, models


def backfill_message_activity(apps, schema_editor):
    SupportCase = apps.get_model("support", "SupportCase")
    SupportMessage = apps.get_model("support", "SupportMessage")
    for case_id in SupportCase.objects.values_list("id", flat=True).iterator():
        last_user = (
            SupportMessage.objects.filter(case_id=case_id, sender__is_staff=False)
            .order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        last_operator = (
            SupportMessage.objects.filter(case_id=case_id, sender__is_staff=True)
            .order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        SupportCase.objects.filter(id=case_id).update(
            last_user_message_at=last_user,
            last_operator_message_at=last_operator,
        )


class Migration(migrations.Migration):
    dependencies = [("support", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="supportcase",
            name="close_reason",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="supportcase",
            name="closed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="supportcase",
            name="last_operator_message_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="supportcase",
            name="last_user_message_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_message_activity, migrations.RunPython.noop),
    ]

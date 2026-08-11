from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="mastercategoryprice",
            name="status",
            field=models.CharField(
                choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")],
                default="approved",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="mastercategoryprice",
            name="reject_reason",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="mastercategoryprice",
            name="experience_years",
            field=models.CharField(blank=True, default="", max_length=16),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="mastercategoryprice",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, null=True, blank=True),
        ),
    ]

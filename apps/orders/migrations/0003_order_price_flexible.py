from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0002_order_budget_ceiling_uzs_order_scheduled_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="price_flexible",
            field=models.BooleanField(default=False),
        ),
    ]

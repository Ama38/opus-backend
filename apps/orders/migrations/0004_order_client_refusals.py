from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0003_order_price_flexible"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="client_refusals",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="order",
            name="needs_operator",
            field=models.BooleanField(default=False),
        ),
    ]

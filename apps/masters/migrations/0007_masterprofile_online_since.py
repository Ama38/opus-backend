from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("masters", "0006_repair_portfolio_post_updated_at")]

    operations = [
        migrations.AddField(
            model_name="masterprofile",
            name="online_since",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

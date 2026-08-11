from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0002_packages_subscription"),
    ]

    operations = [
        migrations.AddField(
            model_name="mastersubscription",
            name="reminder_marker",
            field=models.CharField(blank=True, max_length=8),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0003_masterportfolioitem"),
    ]

    operations = [
        migrations.AddField(
            model_name="masterprofile",
            name="leaderboard_rank",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="masterprofile",
            name="leaderboard_rank_prev",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]

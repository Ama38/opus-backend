from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="PlatformSettings",
            fields=[
                ("singleton_id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ("match_weight_distance", models.FloatField(default=0.5, verbose_name="Вес: расстояние")),
                ("match_weight_rating", models.FloatField(default=0.3, verbose_name="Вес: рейтинг")),
                ("match_weight_completion", models.FloatField(default=0.1, verbose_name="Вес: завершённость")),
                ("match_weight_reaction", models.FloatField(default=0.1, verbose_name="Вес: скорость реакции")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Настройки платформы",
                "verbose_name_plural": "Настройки платформы",
            },
        ),
    ]

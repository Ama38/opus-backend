import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("masters", "0001_initial"),
        ("billing", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Package",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(unique=True)),
                ("name_ru", models.CharField(max_length=120)),
                ("name_uz", models.CharField(max_length=120)),
                ("orders_count", models.PositiveIntegerField()),
                ("price_uzs", models.PositiveIntegerField()),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=100)),
            ],
            options={"ordering": ["sort_order", "orders_count"]},
        ),
        migrations.CreateModel(
            name="MasterSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("orders_remaining", models.PositiveIntegerField(default=0)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("is_frozen", models.BooleanField(default=False)),
                ("frozen_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "master",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subscription",
                        to="masters.masterprofile",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="PackagePurchase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("orders_count", models.PositiveIntegerField()),
                ("price_uzs", models.PositiveIntegerField(default=0)),
                ("is_free", models.BooleanField(default=False)),
                (
                    "status",
                    models.CharField(
                        choices=[("pending", "Pending"), ("activated", "Activated"), ("rejected", "Rejected")],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                (
                    "activated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="activated_package_purchases",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "master",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="package_purchases",
                        to="masters.masterprofile",
                    ),
                ),
                (
                    "package",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="purchases",
                        to="billing.package",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]

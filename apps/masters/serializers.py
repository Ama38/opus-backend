from rest_framework import serializers

from apps.billing.models import MasterWallet

from .models import MasterCategoryPrice, MasterPortfolioItem, MasterProfile, ServiceCategory


class MasterPortfolioItemSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    category_slug = serializers.CharField(source="category.slug", read_only=True, default=None)

    class Meta:
        model = MasterPortfolioItem
        fields = ["id", "image", "image_url", "caption", "category", "category_slug", "created_at"]
        read_only_fields = ["created_at"]
        extra_kwargs = {"image": {"write_only": True}, "category": {"required": False}}

    def get_image_url(self, obj) -> str:
        if not obj.image:
            return ""
        request = self.context.get("request")
        url = obj.image.url
        return request.build_absolute_uri(url) if request is not None else url


class ServiceCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceCategory
        fields = ["id", "slug", "name_ru", "name_uz", "icon", "color_hex", "is_active", "sort_order"]


class MasterCategoryPriceSerializer(serializers.ModelSerializer):
    category = ServiceCategorySerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=ServiceCategory.objects.filter(is_active=True),
        source="category",
        write_only=True,
    )

    class Meta:
        model = MasterCategoryPrice
        fields = [
            "id",
            "category",
            "category_id",
            "min_price_uzs",
            "max_price_uzs",
            "is_active",
            "status",
            "reject_reason",
            "experience_years",
        ]
        read_only_fields = ["status", "reject_reason"]

    def validate(self, attrs):
        if "min_price_uzs" in attrs and "max_price_uzs" in attrs:
            if attrs["min_price_uzs"] > attrs["max_price_uzs"]:
                raise serializers.ValidationError(
                    {"price": "min_price_must_be_less_or_equal_max_price"}
                )
        return attrs


class MasterWalletInlineSerializer(serializers.ModelSerializer):
    class Meta:
        model = MasterWallet
        fields = ["balance_uzs", "package_orders_remaining", "free_orders_remaining"]


class MasterProfileSerializer(serializers.ModelSerializer):
    user_phone = serializers.CharField(source="user.phone", read_only=True)
    user_full_name = serializers.CharField(source="user.full_name", read_only=True)
    category_prices = MasterCategoryPriceSerializer(many=True, required=False)
    wallet = MasterWalletInlineSerializer(read_only=True)

    class Meta:
        model = MasterProfile
        fields = [
            "id",
            "user_phone",
            "user_full_name",
            "status",
            "bio",
            "face_photo_url",
            "activity_points",
            "rating",
            "completed_orders_count",
            "is_online",
            "current_latitude",
            "current_longitude",
            "last_seen_at",
            "wallet",
            "category_prices",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "status",
            "activity_points",
            "rating",
            "completed_orders_count",
            "is_online",
            "last_seen_at",
            "created_at",
            "updated_at",
        ]

    def create(self, validated_data):
        category_prices = validated_data.pop("category_prices", [])
        validated_data.pop("user", None)
        profile, _ = MasterProfile.objects.update_or_create(
            user=self.context["request"].user,
            defaults=validated_data,
        )
        MasterWallet.objects.get_or_create(master=profile)
        for item in category_prices:
            MasterCategoryPrice.objects.update_or_create(
                master=profile,
                category=item["category"],
                defaults={
                    "min_price_uzs": item["min_price_uzs"],
                    "max_price_uzs": item["max_price_uzs"],
                    "is_active": item.get("is_active", True),
                },
            )
        return profile

    def update(self, instance, validated_data):
        category_prices = validated_data.pop("category_prices", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if category_prices is not None:
            for item in category_prices:
                MasterCategoryPrice.objects.update_or_create(
                    master=instance,
                    category=item["category"],
                    defaults={
                        "min_price_uzs": item["min_price_uzs"],
                        "max_price_uzs": item["max_price_uzs"],
                        "is_active": item.get("is_active", True),
                    },
                )
        return instance


class MasterLocationSerializer(serializers.Serializer):
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6)


# ~300m privacy grid: snap public coordinates to a coarse cell so the exact
# home/standing point of a master is never exposed to clients (~0.003°≈330m).
_PRIVACY_GRID_DEG = 0.003


class MasterPublicSerializer(serializers.ModelSerializer):
    """Client-facing master card. Never exposes exact location or phone."""

    full_name = serializers.CharField(source="user.full_name", read_only=True)
    effective_rating = serializers.SerializerMethodField()
    categories = serializers.SerializerMethodField()
    portfolio = serializers.SerializerMethodField()
    rank_change = serializers.SerializerMethodField()
    approx_latitude = serializers.SerializerMethodField()
    approx_longitude = serializers.SerializerMethodField()

    class Meta:
        model = MasterProfile
        fields = [
            "id",
            "full_name",
            "bio",
            "face_photo_url",
            "rating",
            "effective_rating",
            "completed_orders_count",
            "is_online",
            "categories",
            "portfolio",
            "leaderboard_rank",
            "rank_change",
            "approx_latitude",
            "approx_longitude",
        ]

    def get_rank_change(self, obj):
        """Positions gained since the last snapshot: +N up, -N down, null if new
        to the board or unranked."""
        if obj.leaderboard_rank is None or obj.leaderboard_rank_prev is None:
            return None
        return obj.leaderboard_rank_prev - obj.leaderboard_rank

    def get_portfolio(self, obj) -> list:
        items = obj.portfolio_items.all()[:12]
        request = self.context.get("request")
        result = []
        for item in items:
            if not item.image:
                continue
            url = item.image.url
            result.append({
                "id": item.id,
                "image_url": request.build_absolute_uri(url) if request is not None else url,
                "caption": item.caption,
            })
        return result

    def get_effective_rating(self, obj) -> float:
        from django.conf import settings

        threshold = int(getattr(settings, "MASTERGO_NEWCOMER_ORDER_THRESHOLD", 10))
        starter = float(getattr(settings, "MASTERGO_STARTER_RATING", 4.5))
        rating = float(obj.rating or 0)
        if obj.completed_orders_count < threshold:
            return round(max(rating, starter), 2)
        return round(rating, 2)

    def get_categories(self, obj) -> list:
        prices = [
            price
            for price in obj.category_prices.all()
            if price.is_active and price.category is not None
        ]
        return [
            {
                "id": price.category.id,
                "slug": price.category.slug,
                "name_ru": price.category.name_ru,
                "min_price_uzs": price.min_price_uzs,
                "max_price_uzs": price.max_price_uzs,
            }
            for price in prices
        ]

    def _snap(self, value):
        if value is None:
            return None
        return round(round(float(value) / _PRIVACY_GRID_DEG) * _PRIVACY_GRID_DEG, 6)

    def get_approx_latitude(self, obj):
        return self._snap(obj.current_latitude)

    def get_approx_longitude(self, obj):
        return self._snap(obj.current_longitude)


class MasterAnalyticsScheduleItemSerializer(serializers.Serializer):
    order_id = serializers.UUIDField()
    category = serializers.CharField()
    scheduled_at = serializers.DateTimeField(allow_null=True)
    status = serializers.CharField()
    amount_uzs = serializers.IntegerField()
    address = serializers.CharField()


class MasterAnalyticsSerializer(serializers.Serializer):
    earned_today_uzs = serializers.IntegerField()
    earned_yesterday_uzs = serializers.IntegerField()
    orders_today = serializers.IntegerField()
    acceptance_rate_percent = serializers.IntegerField()
    rating_avg = serializers.DecimalField(max_digits=3, decimal_places=2)
    total_orders = serializers.IntegerField()
    schedule_today = MasterAnalyticsScheduleItemSerializer(many=True)

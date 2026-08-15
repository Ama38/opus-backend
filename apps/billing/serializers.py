from rest_framework import serializers

from .models import (
    MasterLedgerEntry,
    MasterSubscription,
    MasterWallet,
    Package,
    PackagePurchase,
)


class PackageSerializer(serializers.ModelSerializer):
    # True during the free launch period: buying activates the package instantly
    # at no cost. The app shows a "Бесплатно" badge and strikes the price.
    is_free = serializers.SerializerMethodField()

    class Meta:
        model = Package
        fields = [
            "id",
            "slug",
            "name_ru",
            "name_uz",
            "orders_count",
            "price_uzs",
            "is_free",
            "sort_order",
        ]

    def get_is_free(self, obj) -> bool:
        from .services import free_packages_enabled

        return free_packages_enabled()


class PackagePurchaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = PackagePurchase
        fields = ["id", "package", "orders_count", "price_uzs", "is_free", "status", "created_at", "activated_at"]


class MasterSubscriptionSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    days_left = serializers.IntegerField(read_only=True)

    class Meta:
        model = MasterSubscription
        fields = [
            "orders_remaining",
            "activated_at",
            "expires_at",
            "is_frozen",
            "is_active",
            "is_expired",
            "days_left",
        ]
        read_only_fields = fields


class MasterLedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = MasterLedgerEntry
        fields = ["id", "entry_type", "amount_uzs", "balance_after_uzs", "note", "created_at"]


class MasterWalletSerializer(serializers.ModelSerializer):
    ledger_entries = MasterLedgerEntrySerializer(many=True, read_only=True)

    class Meta:
        model = MasterWallet
        fields = [
            "id",
            "master",
            "balance_uzs",
            "package_orders_remaining",
            "free_orders_remaining",
            "ledger_entries",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["master", "balance_uzs", "ledger_entries", "created_at", "updated_at"]


class WalletTopUpSerializer(serializers.Serializer):
    amount_uzs = serializers.IntegerField(min_value=1)
    note = serializers.CharField(required=False, allow_blank=True)


from django.contrib import admin

from .models import (
    MasterLedgerEntry,
    MasterSubscription,
    MasterWallet,
    Package,
    PackagePurchase,
)
from .services import activate_purchase, freeze_subscription, top_up_wallet, unfreeze_subscription


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    list_display = ["name_ru", "orders_count", "price_uzs", "is_active", "sort_order"]
    list_editable = ["price_uzs", "is_active", "sort_order"]
    search_fields = ["slug", "name_ru", "name_uz"]


@admin.register(MasterSubscription)
class MasterSubscriptionAdmin(admin.ModelAdmin):
    list_display = ["master", "orders_remaining", "expires_at", "is_frozen", "is_active", "days_left"]
    list_filter = ["is_frozen", "expires_at"]
    search_fields = ["master__user__phone", "master__user__full_name"]
    readonly_fields = ["created_at", "updated_at", "is_active", "is_expired", "days_left"]
    actions = ["freeze", "unfreeze"]

    @admin.action(description="Freeze subscription (pause days)")
    def freeze(self, request, queryset):
        for sub in queryset.select_related("master"):
            freeze_subscription(sub.master)
        self.message_user(request, f"Frozen {queryset.count()} subscription(s).")

    @admin.action(description="Unfreeze subscription (resume days)")
    def unfreeze(self, request, queryset):
        for sub in queryset.select_related("master"):
            unfreeze_subscription(sub.master)
        self.message_user(request, f"Unfrozen {queryset.count()} subscription(s).")


@admin.register(PackagePurchase)
class PackagePurchaseAdmin(admin.ModelAdmin):
    list_display = ["master", "package", "orders_count", "price_uzs", "is_free", "status", "created_at"]
    list_filter = ["status", "is_free", "created_at"]
    search_fields = ["master__user__phone", "master__user__full_name"]
    readonly_fields = ["created_at", "activated_at", "activated_by"]
    actions = ["activate_selected"]

    @admin.action(description="Payment received — activate package")
    def activate_selected(self, request, queryset):
        count = 0
        for purchase in queryset.select_related("master"):
            activate_purchase(purchase, activated_by=request.user)
            count += 1
        self.message_user(request, f"Activated {count} package(s).")


class MasterLedgerEntryInline(admin.TabularInline):
    model = MasterLedgerEntry
    extra = 0
    readonly_fields = ["entry_type", "amount_uzs", "balance_after_uzs", "note", "created_by", "created_at"]
    can_delete = False


@admin.register(MasterWallet)
class MasterWalletAdmin(admin.ModelAdmin):
    list_display = ["master", "balance_uzs", "package_orders_remaining", "free_orders_remaining", "updated_at"]
    list_filter = ["updated_at", "free_orders_remaining", "package_orders_remaining"]
    search_fields = ["master__user__phone", "master__user__full_name"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [MasterLedgerEntryInline]
    actions = ["top_up_40000", "top_up_50000", "top_up_100000", "reset_free_orders"]

    def _top_up_selected(self, request, queryset, amount_uzs: int) -> None:
        for wallet in queryset.select_related("master__user"):
            top_up_wallet(
                wallet,
                amount_uzs,
                note=f"Admin manual top-up {amount_uzs} UZS",
                created_by=request.user,
            )
        self.message_user(request, f"Topped up {queryset.count()} wallet(s) by {amount_uzs} UZS.")

    @admin.action(description="Top up selected wallets by 40,000 UZS")
    def top_up_40000(self, request, queryset):
        self._top_up_selected(request, queryset, 40_000)

    @admin.action(description="Top up selected wallets by 50,000 UZS")
    def top_up_50000(self, request, queryset):
        self._top_up_selected(request, queryset, 50_000)

    @admin.action(description="Top up selected wallets by 100,000 UZS")
    def top_up_100000(self, request, queryset):
        self._top_up_selected(request, queryset, 100_000)

    @admin.action(description="Reset selected wallets to 10 free orders")
    def reset_free_orders(self, request, queryset):
        updated = queryset.update(free_orders_remaining=10)
        self.message_user(request, f"Reset free orders for {updated} wallet(s).")


@admin.register(MasterLedgerEntry)
class MasterLedgerEntryAdmin(admin.ModelAdmin):
    list_display = ["wallet", "entry_type", "amount_uzs", "balance_after_uzs", "created_at"]
    list_filter = ["entry_type"]
    search_fields = ["wallet__master__user__phone", "wallet__master__user__full_name", "note"]
    readonly_fields = ["created_at"]

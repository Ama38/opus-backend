from django.contrib import admin

from .models import (
    MasterCategoryPrice,
    MasterPortfolioPost,
    MasterPortfolioPostImage,
    MasterProfile,
    MasterServiceStatus,
    MasterStatus,
    ServiceCategory,
)


class MasterPortfolioPostImageInline(admin.TabularInline):
    model = MasterPortfolioPostImage
    extra = 0
    readonly_fields = ["created_at"]


@admin.register(MasterPortfolioPost)
class MasterPortfolioPostAdmin(admin.ModelAdmin):
    list_display = ["title", "master", "category", "created_at"]
    list_filter = ["category", "created_at"]
    search_fields = [
        "title",
        "description",
        "master__user__phone",
        "master__user__full_name",
    ]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [MasterPortfolioPostImageInline]


@admin.register(MasterCategoryPrice)
class MasterCategoryPriceAdmin(admin.ModelAdmin):
    list_display = ["master", "category", "status", "is_active", "min_price_uzs", "max_price_uzs"]
    list_filter = ["status", "is_active", "category"]
    search_fields = ["master__user__phone", "master__user__full_name", "category__name_ru"]
    actions = ["approve_services", "reject_services"]

    @admin.action(description="Approve selected services")
    def approve_services(self, request, queryset):
        updated = queryset.update(status=MasterServiceStatus.APPROVED, reject_reason="")
        self.message_user(request, f"Approved {updated} service(s).")

    @admin.action(description="Reject selected services")
    def reject_services(self, request, queryset):
        updated = queryset.update(status=MasterServiceStatus.REJECTED, is_active=False)
        self.message_user(request, f"Rejected {updated} service(s).")


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ["name_ru", "name_uz", "slug", "icon", "is_active", "sort_order"]
    list_filter = ["is_active"]
    search_fields = ["name_ru", "name_uz", "slug"]
    prepopulated_fields = {"slug": ["name_ru"]}


class MasterCategoryPriceInline(admin.TabularInline):
    model = MasterCategoryPrice
    extra = 1


@admin.register(MasterProfile)
class MasterProfileAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "status",
        "is_online",
        "rating",
        "activity_points",
        "completed_orders_count",
        "last_seen_at",
        "approved_at",
        "blocked_at",
    ]
    list_filter = ["status", "is_online", "created_at", "approved_at", "blocked_at"]
    search_fields = ["user__phone", "user__full_name", "bio"]
    readonly_fields = ["approved_at", "blocked_at", "created_at", "updated_at", "last_seen_at"]
    inlines = [MasterCategoryPriceInline]
    actions = ["approve_masters", "reject_masters", "block_masters", "take_offline", "bring_online"]

    @admin.action(description="Approve selected masters")
    def approve_masters(self, request, queryset):
        for master in queryset:
            master.approve()

    @admin.action(description="Reject selected masters")
    def reject_masters(self, request, queryset):
        updated = queryset.update(status=MasterStatus.REJECTED, is_online=False)
        for master in queryset.select_related("user"):
            master.user.is_master_enabled = False
            master.user.save(update_fields=["is_master_enabled", "updated_at"])
        self.message_user(request, f"Rejected {updated} master(s).")

    @admin.action(description="Block selected masters")
    def block_masters(self, request, queryset):
        updated = queryset.update(status=MasterStatus.BLOCKED, is_online=False)
        for master in queryset.select_related("user"):
            master.user.is_master_enabled = False
            master.user.save(update_fields=["is_master_enabled", "updated_at"])
        self.message_user(request, f"Blocked {updated} master(s).")

    @admin.action(description="Take selected masters offline")
    def take_offline(self, request, queryset):
        updated = queryset.update(is_online=False)
        self.message_user(request, f"Took {updated} master(s) offline.")

    @admin.action(description="Bring approved masters online")
    def bring_online(self, request, queryset):
        updated = queryset.filter(status=MasterStatus.APPROVED).update(is_online=True)
        self.message_user(request, f"Moved {updated} approved master(s) online.")

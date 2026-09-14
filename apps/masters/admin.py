from django.contrib import admin
from django.db.models import Case, IntegerField, Value, When
from django.urls import reverse
from django.utils.html import format_html

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
    verbose_name = "Фото"
    verbose_name_plural = "Фото поста"


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

    @admin.action(description="Подтвердить выбранные услуги")
    def approve_services(self, request, queryset):
        updated = queryset.update(status=MasterServiceStatus.APPROVED, reject_reason="")
        self.message_user(request, f"Подтверждено услуг: {updated}.")

    @admin.action(description="Отклонить выбранные услуги")
    def reject_services(self, request, queryset):
        updated = queryset.update(status=MasterServiceStatus.REJECTED, is_active=False)
        self.message_user(request, f"Отклонено услуг: {updated}.")


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ["name_ru", "name_uz", "slug", "icon", "is_active", "sort_order"]
    list_filter = ["is_active"]
    search_fields = ["name_ru", "name_uz", "slug"]
    prepopulated_fields = {"slug": ["name_ru"]}


class MasterCategoryPriceInline(admin.TabularInline):
    """Editable right on the master's page, so a service added *after*
    approval (which comes back in as pending) is moderated here too — no
    separate trip to the standalone MasterCategoryPrice list required."""

    model = MasterCategoryPrice
    extra = 0
    fields = ["category", "status", "is_active", "min_price_uzs", "max_price_uzs", "reject_reason"]
    verbose_name = "Услуга"
    verbose_name_plural = "Услуги мастера"


@admin.register(MasterProfile)
class MasterProfileAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "phone",
        "status",
        "pending_services",
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
    readonly_fields = [
        "identity_summary",
        "approved_at",
        "blocked_at",
        "created_at",
        "updated_at",
        "last_seen_at",
    ]
    inlines = [MasterCategoryPriceInline]
    actions = ["approve_masters", "reject_masters", "block_masters", "take_offline", "bring_online"]
    fieldsets = [
        ("Кто это", {"fields": ["identity_summary"]}),
        (
            "Подтверждение",
            {"fields": ["status", "bio", "approved_at", "blocked_at", "block_reason"]},
        ),
        (
            "Активность",
            {
                "fields": [
                    "is_online",
                    "online_since",
                    "last_seen_at",
                    "rating",
                    "activity_points",
                    "completed_orders_count",
                ]
            },
        ),
        ("Рейтинг недели", {"fields": ["leaderboard_rank", "leaderboard_rank_prev"], "classes": ["collapse"]}),
        (
            "Геолокация",
            {"fields": ["current_latitude", "current_longitude"], "classes": ["collapse"]},
        ),
        (
            "Служебное",
            {"fields": ["user", "face_photo_url", "created_at", "updated_at"], "classes": ["collapse"]},
        ),
    ]

    def save_model(self, request, obj, form, change):
        # `status` is a plain editable field on this form (so an operator can
        # change it without hunting for the bulk action), but MasterProfile's
        # own approve()/reject()/block() do more than set the status string --
        # they flip user.is_master_enabled and (for approve) cascade-approve
        # the master's pending services. A bare save would leave the status
        # looking "Подтверждён" while the mobile app stays locked out, because
        # it gates access on is_master_enabled, not on this field directly.
        previous_status = None
        if change and obj.pk:
            previous_status = (
                MasterProfile.objects.filter(pk=obj.pk).values_list("status", flat=True).first()
            )
        super().save_model(request, obj, form, change)
        if previous_status is not None and previous_status != obj.status:
            if obj.status == MasterStatus.APPROVED:
                obj.approve()
            elif obj.status == MasterStatus.REJECTED:
                obj.reject()
            elif obj.status == MasterStatus.BLOCKED:
                obj.block()

    def get_queryset(self, request):
        # Masters waiting for approval float to the top -- that's the queue
        # an operator actually works from, not alphabetical/id order.
        priority = Case(
            When(status=MasterStatus.PENDING, then=Value(0)),
            When(status=MasterStatus.APPROVED, then=Value(1)),
            When(status=MasterStatus.REJECTED, then=Value(2)),
            default=Value(3),
            output_field=IntegerField(),
        )
        return (
            super()
            .get_queryset(request)
            .select_related("user")
            .annotate(_status_priority=priority)
            .order_by("_status_priority", "-created_at")
        )

    @admin.display(description="Телефон", ordering="user__phone")
    def phone(self, obj):
        return obj.user.phone

    @admin.display(description="Новые услуги на проверку")
    def pending_services(self, obj):
        count = obj.category_prices.filter(status=MasterServiceStatus.PENDING).count()
        return f"{count}" if count else "—"

    @admin.display(description="Личные данные")
    def identity_summary(self, obj):
        # Everything an operator needs to decide whether to approve this
        # master, without leaving this page: who they are, their MyID
        # verification result, and a link to their full account if needed.
        user = obj.user
        if user.myid_verified_at:
            myid_html = format_html(
                '<span style="color:#28a745">✅ верифицирован MyID {}</span>',
                user.myid_verified_at.strftime("%d.%m.%Y %H:%M"),
            )
        else:
            myid_html = format_html('<span style="color:#dc3545">❌ не верифицирован MyID</span>')
        avatar_html = (
            format_html('<img src="{}" style="max-width:110px;border-radius:8px">', user.avatar.url)
            if user.avatar
            else ""
        )
        user_url = reverse("admin:accounts_user_change", args=[user.pk])
        return format_html(
            '<div style="display:flex;gap:16px;align-items:flex-start">'
            "<div>{avatar}</div>"
            "<div>"
            '<div style="font-size:16px;font-weight:600">{name}</div>'
            "<div>Телефон: {phone}</div>"
            "<div>Дата рождения: {dob}</div>"
            "<div>{myid}</div>"
            "<div>ПИНФЛ: {pinfl}</div>"
            '<div style="margin-top:6px"><a href="{url}">Открыть карточку пользователя →</a></div>'
            "</div>"
            "</div>",
            avatar=avatar_html,
            name=user.full_name or "— имя не указано —",
            phone=user.phone,
            dob=user.birth_date.strftime("%d.%m.%Y") if user.birth_date else "—",
            myid=myid_html,
            pinfl=user.pinfl or "—",
            url=user_url,
        )

    @admin.action(description="Подтвердить выбранных мастеров (и их услуги)")
    def approve_masters(self, request, queryset):
        for master in queryset:
            master.approve()
        self.message_user(request, f"Подтверждено мастеров: {queryset.count()}.")

    @admin.action(description="Отклонить выбранных мастеров")
    def reject_masters(self, request, queryset):
        for master in queryset:
            master.reject()
        self.message_user(request, f"Отклонено мастеров: {queryset.count()}.")

    @admin.action(description="Заблокировать выбранных мастеров")
    def block_masters(self, request, queryset):
        for master in queryset:
            master.block()
        self.message_user(request, f"Заблокировано мастеров: {queryset.count()}.")

    @admin.action(description="Перевести выбранных мастеров в оффлайн")
    def take_offline(self, request, queryset):
        updated = queryset.update(is_online=False)
        self.message_user(request, f"Переведено в оффлайн: {updated}.")

    @admin.action(description="Перевести подтверждённых мастеров в онлайн")
    def bring_online(self, request, queryset):
        updated = queryset.filter(status=MasterStatus.APPROVED).update(is_online=True)
        self.message_user(request, f"Переведено в онлайн: {updated}.")

from django.contrib import admin

from .models import DeviceToken, NotificationEvent, NotificationStatus


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ["user", "platform", "is_active", "updated_at"]
    list_filter = ["platform", "is_active", "updated_at"]
    search_fields = ["user__phone", "user__full_name", "token"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(NotificationEvent)
class NotificationEventAdmin(admin.ModelAdmin):
    list_display = ["user", "channel", "event_type", "status", "created_at", "sent_at"]
    list_filter = ["channel", "event_type", "status", "created_at", "sent_at"]
    search_fields = ["user__phone", "user__full_name", "title", "body"]
    readonly_fields = ["created_at", "sent_at"]
    date_hierarchy = "created_at"
    actions = ["mark_pending", "mark_sent", "mark_failed", "mark_skipped"]

    @admin.action(description="Отметить как ожидающие")
    def mark_pending(self, request, queryset):
        self._set_status(request, queryset, NotificationStatus.PENDING)

    @admin.action(description="Отметить как отправленные")
    def mark_sent(self, request, queryset):
        self._set_status(request, queryset, NotificationStatus.SENT)

    @admin.action(description="Отметить как с ошибкой")
    def mark_failed(self, request, queryset):
        self._set_status(request, queryset, NotificationStatus.FAILED)

    @admin.action(description="Отметить как пропущенные")
    def mark_skipped(self, request, queryset):
        self._set_status(request, queryset, NotificationStatus.SKIPPED)

    def _set_status(self, request, queryset, status: str):
        updated = queryset.update(status=status)
        self.message_user(request, f"Обновлено уведомлений: {updated} → {status}.")

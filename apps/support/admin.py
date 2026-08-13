from django import forms
from django.contrib import admin

from .models import SupportCase, SupportCaseStatus, SupportMessage
from .services import add_support_message


class SupportCaseAdminForm(forms.ModelForm):
    operator_reply = forms.CharField(
        label="Ответ клиенту/мастеру",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "Напишите ответ — он появится в чате приложения",
            }
        ),
        help_text="Ответ сохранится как сообщение текущего оператора.",
    )

    class Meta:
        model = SupportCase
        fields = "__all__"


class SupportMessageInline(admin.TabularInline):
    model = SupportMessage
    extra = 0
    readonly_fields = ["sender", "text", "created_at"]


@admin.register(SupportCase)
class SupportCaseAdmin(admin.ModelAdmin):
    form = SupportCaseAdminForm
    list_display = ["subject", "user", "order", "status", "priority", "assigned_to", "created_at"]
    list_filter = ["status", "priority", "assigned_to", "created_at", "updated_at"]
    search_fields = ["subject", "body", "user__phone", "user__full_name", "order__id"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [SupportMessageInline]
    actions = [
        "assign_to_me",
        "mark_open",
        "mark_in_progress",
        "mark_resolved",
        "mark_closed",
    ]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        reply = form.cleaned_data.get("operator_reply", "").strip()
        if reply:
            add_support_message(obj, sender=request.user, text=reply)

    @admin.action(description="Assign selected cases to me")
    def assign_to_me(self, request, queryset):
        updated = queryset.update(assigned_to=request.user)
        self.message_user(request, f"Assigned {updated} support case(s).")

    @admin.action(description="Mark selected cases as open")
    def mark_open(self, request, queryset):
        self._set_status(request, queryset, SupportCaseStatus.OPEN)

    @admin.action(description="Mark selected cases in progress")
    def mark_in_progress(self, request, queryset):
        self._set_status(request, queryset, SupportCaseStatus.IN_PROGRESS)

    @admin.action(description="Mark selected cases as resolved")
    def mark_resolved(self, request, queryset):
        self._set_status(request, queryset, SupportCaseStatus.RESOLVED)

    @admin.action(description="Mark selected cases as closed")
    def mark_closed(self, request, queryset):
        self._set_status(request, queryset, SupportCaseStatus.CLOSED)

    def _set_status(self, request, queryset, status: str):
        updated = queryset.update(status=status)
        self.message_user(request, f"Updated {updated} support case(s) to {status}.")


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = ["case", "sender", "created_at"]
    search_fields = ["case__subject", "sender__phone", "sender__full_name", "text"]
    readonly_fields = ["created_at"]

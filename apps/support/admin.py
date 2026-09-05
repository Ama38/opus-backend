from django import forms
from django.contrib import admin
from django.db import transaction
from django.utils import timezone

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

    def clean(self):
        data = super().clean()
        if self.instance.pk and data.get("operator_reply", "").strip():
            current = SupportCase.objects.get(pk=self.instance.pk)
            if current.status in {SupportCaseStatus.RESOLVED, SupportCaseStatus.CLOSED}:
                self.add_error("operator_reply", "Обращение закрыто. Сначала откройте его заново.")
        return data


class SupportMessageInline(admin.TabularInline):
    """Operator conversation view. Existing messages are read-only; the operator
    types replies in the blank row(s) — sender is auto-set to the operator."""

    model = SupportMessage
    extra = 1
    fields = ["sender", "text", "created_at"]
    readonly_fields = ["sender", "created_at"]

    def has_change_permission(self, request, obj=None):
        return False  # existing messages are immutable; only new replies allowed

    def has_add_permission(self, request, obj=None):
        return (obj is None or obj.status not in {
            SupportCaseStatus.RESOLVED, SupportCaseStatus.CLOSED,
        }) and super().has_add_permission(request, obj)


@admin.register(SupportCase)
class SupportCaseAdmin(admin.ModelAdmin):
    form = SupportCaseAdminForm
    list_display = ["subject", "user", "order", "status", "priority", "assigned_to", "created_at"]
    list_filter = ["status", "priority", "assigned_to", "created_at", "updated_at"]
    search_fields = ["subject", "body", "user__phone", "user__full_name", "order__id"]
    readonly_fields = [
        "last_user_message_at", "last_operator_message_at", "closed_at",
        "close_reason", "created_at", "updated_at",
    ]
    inlines = [SupportMessageInline]

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            if isinstance(instance, SupportMessage):
                add_support_message(
                    form.instance,
                    sender=instance.sender if instance.sender_id else request.user,
                    text=instance.text,
                )
            else:
                instance.save()
        formset.save_m2m()
    actions = [
        "assign_to_me",
        "mark_open",
        "mark_in_progress",
        "mark_resolved",
        "mark_closed",
    ]

    def save_model(self, request, obj, form, change):
        # Django wraps the whole change form (including inlines) in a transaction.
        requested_status = obj.status
        previous = SupportCase.objects.select_for_update().filter(pk=obj.pk).first()
        obj._requested_support_status = requested_status
        if requested_status in {SupportCaseStatus.RESOLVED, SupportCaseStatus.CLOSED}:
            obj.status = previous.status if previous else SupportCaseStatus.OPEN
        elif previous and previous.status in {SupportCaseStatus.RESOLVED, SupportCaseStatus.CLOSED}:
            obj.closed_at = None
            obj.close_reason = ""
        super().save_model(request, obj, form, change)
        reply = form.cleaned_data.get("operator_reply", "").strip()
        if reply:
            add_support_message(obj, sender=request.user, text=reply)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance
        requested = getattr(obj, "_requested_support_status", obj.status)
        if requested in {SupportCaseStatus.RESOLVED, SupportCaseStatus.CLOSED}:
            self._transition_status(SupportCase.objects.filter(pk=obj.pk), requested)
            obj.refresh_from_db()

    @staticmethod
    @transaction.atomic
    def _transition_status(queryset, status):
        now = timezone.now()
        terminal = status in {SupportCaseStatus.RESOLVED, SupportCaseStatus.CLOSED}
        for case in queryset.select_for_update():
            if case.status == status:
                continue
            case.status = status
            case.closed_at = now if terminal else None
            case.close_reason = "resolved_by_operator" if terminal else ""
            case.save(update_fields=["status", "closed_at", "close_reason", "updated_at"])

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
        updated = queryset.count()
        self._transition_status(queryset, status)
        self.message_user(request, f"Updated {updated} support case(s) to {status}.")


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = ["case", "sender", "created_at"]
    search_fields = ["case__subject", "sender__phone", "sender__full_name", "text"]
    readonly_fields = ["created_at"]

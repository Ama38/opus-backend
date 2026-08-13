from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import path

from .models import OTPChallenge, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    change_list_template = "admin/accounts/user_changelist.html"
    ordering = ["-date_joined"]
    list_display = ["phone", "full_name", "language", "is_client_enabled", "is_master_enabled", "is_staff"]
    list_filter = ["language", "is_client_enabled", "is_master_enabled", "is_staff", "is_active"]
    search_fields = ["phone", "full_name"]
    fieldsets = [
        (None, {"fields": ["phone", "password"]}),
        ("Profile", {"fields": ["full_name", "avatar_url", "language"]}),
        (
            "Roles",
            {"fields": ["is_client_enabled", "is_master_enabled"]},
        ),
        ("Permissions", {"fields": ["is_active", "is_staff", "is_superuser", "groups", "user_permissions"]}),
        ("Dates", {"fields": ["last_login", "date_joined", "updated_at"]}),
    ]
    # is_master_enabled can also be flipped from the Master profile approve/reject
    # actions, but operators may edit it directly here too.
    readonly_fields = ["date_joined", "updated_at", "last_login"]
    add_fieldsets = [
        (
            None,
            {
                "classes": ["wide"],
                "fields": ["phone", "password1", "password2", "is_staff", "is_superuser"],
            },
        )
    ]

    # ------------------------------------------------------------------ #
    #  Test-only bulk wipe: two buttons on the user list to clear every   #
    #  master / every client together with all their related data.        #
    # ------------------------------------------------------------------ #
    def get_urls(self):
        custom = [
            path(
                "purge-masters/",
                self.admin_site.admin_view(self.purge_masters_view),
                name="accounts_user_purge_masters",
            ),
            path(
                "purge-clients/",
                self.admin_site.admin_view(self.purge_clients_view),
                name="accounts_user_purge_clients",
            ),
        ]
        return custom + super().get_urls()

    def purge_masters_view(self, request):
        if request.method != "POST":
            return redirect("..")
        try:
            count = _purge_all_masters()
            messages.success(request, f"Удалено мастеров: {count} (со всеми связями).")
        except Exception as error:  # noqa: BLE001 - surface the reason to the operator
            messages.error(request, f"Ошибка очистки мастеров: {error}")
        return redirect("..")

    def purge_clients_view(self, request):
        if request.method != "POST":
            return redirect("..")
        try:
            count = _purge_all_clients()
            messages.success(request, f"Удалено клиентов: {count} (со всеми связями).")
        except Exception as error:  # noqa: BLE001
            messages.error(request, f"Ошибка очистки клиентов: {error}")
        return redirect("..")


@transaction.atomic
def _purge_all_masters() -> int:
    """Delete every master profile and its user account together with all
    related data (services, portfolio, subscription, wallet, offers, proposals,
    chat, reviews, support). Orders keep their client but lose the master link."""
    from apps.chat.models import ChatMessage
    from apps.masters.models import MasterProfile
    from apps.orders.models import MasterOffer, Order, PriceProposal
    from apps.reviews.models import Review
    from apps.support.models import SupportCase, SupportMessage

    master_user_ids = list(
        User.objects.filter(master_profile__isnull=False)
        .exclude(is_superuser=True)
        .values_list("id", flat=True)
    )

    # 1) Break PROTECT links pointing at master profiles.
    Order.objects.filter(master__isnull=False).update(master=None)
    PriceProposal.objects.all().delete()
    MasterOffer.objects.all().delete()
    # 2) Cascade-delete the profiles (category prices, portfolio, subscription,
    #    wallet, package purchases, ledger).
    MasterProfile.objects.all().delete()
    # 3) Break PROTECT links pointing at the master user accounts, then delete.
    users = User.objects.filter(id__in=master_user_ids)
    ChatMessage.objects.filter(sender__in=users).delete()
    Review.objects.filter(Q(author__in=users) | Q(target__in=users)).delete()
    SupportMessage.objects.filter(sender__in=users).delete()
    SupportCase.objects.filter(user__in=users).delete()
    Order.objects.filter(client__in=users).delete()  # dual accounts, if any
    count = users.count()
    users.delete()
    return count


@transaction.atomic
def _purge_all_clients() -> int:
    """Delete every client account (non-staff, non-master) together with all
    their orders and related data (attachments, offers, proposals, events, chat,
    reviews, support)."""
    from apps.chat.models import ChatMessage
    from apps.orders.models import Order
    from apps.reviews.models import Review
    from apps.support.models import SupportCase, SupportMessage

    clients = User.objects.filter(
        is_staff=False, is_superuser=False, master_profile__isnull=True
    )
    client_ids = list(clients.values_list("id", flat=True))
    users = User.objects.filter(id__in=client_ids)

    # Deleting orders cascades attachments, offers, proposals, events, chat room
    # (and its messages) and reviews tied to the order.
    Order.objects.filter(client__in=users).delete()
    # Break any remaining PROTECT links on the user accounts, then delete.
    ChatMessage.objects.filter(sender__in=users).delete()
    Review.objects.filter(Q(author__in=users) | Q(target__in=users)).delete()
    SupportMessage.objects.filter(sender__in=users).delete()
    SupportCase.objects.filter(user__in=users).delete()
    count = users.count()
    users.delete()
    return count


@admin.register(OTPChallenge)
class OTPChallengeAdmin(admin.ModelAdmin):
    list_display = ["phone", "purpose", "attempts", "is_used", "expires_at", "created_at"]
    list_filter = ["purpose", "is_used"]
    search_fields = ["phone"]
    readonly_fields = ["created_at"]


from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import OTPChallenge, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ["-date_joined"]
    list_display = ["phone", "full_name", "language", "is_client_enabled", "is_master_enabled", "is_staff"]
    list_filter = ["language", "is_client_enabled", "is_master_enabled", "is_staff", "is_active"]
    search_fields = ["phone", "full_name"]
    fieldsets = [
        (None, {"fields": ["phone", "password"]}),
        ("Profile", {"fields": ["full_name", "avatar_url", "language"]}),
        (
            "Roles (master status is managed on the Master profile — read-only here)",
            {"fields": ["is_client_enabled", "is_master_enabled"]},
        ),
        ("Permissions", {"fields": ["is_active", "is_staff", "is_superuser", "groups", "user_permissions"]}),
        ("Dates", {"fields": ["last_login", "date_joined", "updated_at"]}),
    ]
    # is_master_enabled mirrors MasterProfile.status (set by approve/reject/block);
    # editing it directly would drift from the moderation truth, so it's read-only.
    readonly_fields = ["date_joined", "updated_at", "last_login", "is_master_enabled"]
    add_fieldsets = [
        (
            None,
            {
                "classes": ["wide"],
                "fields": ["phone", "password1", "password2", "is_staff", "is_superuser"],
            },
        )
    ]


@admin.register(OTPChallenge)
class OTPChallengeAdmin(admin.ModelAdmin):
    list_display = ["phone", "purpose", "attempts", "is_used", "expires_at", "created_at"]
    list_filter = ["purpose", "is_used"]
    search_fields = ["phone"]
    readonly_fields = ["created_at"]


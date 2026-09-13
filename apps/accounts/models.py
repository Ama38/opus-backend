from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class Language(models.TextChoices):
    RU = "ru", "Russian"
    UZ = "uz", "Uzbek"


class UserManager(BaseUserManager):
    def create_user(self, phone: str, password: str | None = None, **extra_fields):
        if not phone:
            raise ValueError("Phone is required")
        user = self.model(phone=phone, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")

        return self.create_user(phone, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    phone = models.CharField(max_length=32, unique=True)
    full_name = models.CharField(max_length=160, blank=True)
    first_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    avatar = models.ImageField(upload_to="avatars/%Y/%m/", blank=True, null=True)
    avatar_url = models.URLField(blank=True)
    language = models.CharField(max_length=2, choices=Language.choices, default=Language.RU)

    # Populated once from MyID.uz after a successful face-scan identification
    # (see apps.accounts.myid). Not user-editable.
    pinfl = models.CharField(max_length=14, blank=True)
    myid_verified_at = models.DateTimeField(null=True, blank=True)

    is_client_enabled = models.BooleanField(default=True)
    is_master_enabled = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    date_joined = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        ordering = ["-date_joined"]

    def __str__(self) -> str:
        return self.full_name or self.phone


class OTPPurpose(models.TextChoices):
    LOGIN = "login", "Login"
    VERIFY_PHONE = "verify_phone", "Verify phone"


class OTPChallenge(models.Model):
    phone = models.CharField(max_length=32, db_index=True)
    purpose = models.CharField(max_length=32, choices=OTPPurpose.choices, default=OTPPurpose.LOGIN)
    code = models.CharField(max_length=12)
    attempts = models.PositiveSmallIntegerField(default=0)
    is_used = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.phone} / {self.purpose}"


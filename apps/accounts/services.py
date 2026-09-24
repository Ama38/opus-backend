from dataclasses import dataclass
from datetime import timedelta
import logging
import random

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.utils import timezone

from .models import OTPPurpose, User
from .sms import SmsError, normalize_phone, send_otp_sms

logger = logging.getLogger("mastergo.otp")


OTP_TTL_SECONDS = 5 * 60
OTP_ATTEMPTS = 3
OTP_RESEND_SECONDS = 60
OTP_HOURLY_LIMIT = 5


@dataclass(frozen=True)
class OTPStartResult:
    phone: str
    expires_at: object


@dataclass(frozen=True)
class OTPVerifyResult:
    user: User
    is_new_user: bool


class OTPError(Exception):
    def __init__(self, code: str, *, attempts_left: int | None = None, retry_after: int | None = None):
        super().__init__(code)
        self.code = code
        self.attempts_left = attempts_left
        self.retry_after = retry_after


def _is_review_phone(phone: str) -> bool:
    """True for the fixed number given to Google Play reviewers in the
    'Sign in details' declaration. Real SMS delivery isn't attempted for it
    (a reviewer has no way to receive one) -- it always gets a fixed code
    instead. Every other phone number is completely unaffected."""
    review_phone = getattr(settings, "MASTERGO_REVIEW_PHONE", "")
    return bool(review_phone) and normalize_phone(phone) == normalize_phone(review_phone)


def start_otp(phone: str, purpose: str = OTPPurpose.LOGIN) -> OTPStartResult:
    now = timezone.now()
    phone_key = _phone_key(phone, purpose)
    resend_key = f"{phone_key}:resend"
    hourly_key = f"{phone_key}:hourly"

    retry_after = cache.get(resend_key)
    if retry_after is not None:
        raise OTPError("otp_throttled", retry_after=int(retry_after))

    hourly_count = int(cache.get(hourly_key) or 0)
    if hourly_count >= OTP_HOURLY_LIMIT:
        raise OTPError("otp_hourly_limit", retry_after=3600)

    is_review_phone = _is_review_phone(phone)
    if getattr(settings, "MASTERGO_MOCK_OTP", False):
        code = str(settings.MASTERGO_MOCK_OTP_CODE)
    elif is_review_phone:
        code = str(getattr(settings, "MASTERGO_REVIEW_OTP_CODE", "0000"))
    else:
        code = f"{random.randint(1000, 9999)}"
    expires_at = now + timedelta(seconds=OTP_TTL_SECONDS)
    cache.set(
        phone_key,
        {
            "code_hash": make_password(code),
            "attempts_left": OTP_ATTEMPTS,
            "expires_at": expires_at.isoformat(),
        },
        timeout=OTP_TTL_SECONDS,
    )
    cache.set(resend_key, OTP_RESEND_SECONDS, timeout=OTP_RESEND_SECONDS)
    cache.set(hourly_key, hourly_count + 1, timeout=60 * 60)

    # In mock mode (or for the reviewer phone) the code is a fixed
    # well-known value, so no SMS is needed. Otherwise deliver it: real
    # gateway in production, console in dry-run/dev.
    if not getattr(settings, "MASTERGO_MOCK_OTP", False) and not is_review_phone:
        try:
            send_otp_sms(phone, code)
        except SmsError as error:
            logger.warning("OTP SMS delivery failed for %s: %s", phone, error)
            # Roll back the throttle window so the user can retry immediately.
            cache.delete(resend_key)
            raise OTPError("otp_send_failed") from error

    if settings.DEBUG:
        logger.info("[MasterGo OTP] phone=%s code=%s expires_at=%s", phone, code, expires_at.isoformat())

    return OTPStartResult(phone=phone, expires_at=expires_at)


def verify_otp(
    *,
    phone: str,
    code: str,
    full_name: str = "",
    language: str | None = None,
    purpose: str = OTPPurpose.LOGIN,
) -> OTPVerifyResult:
    phone_key = _phone_key(phone, purpose)

    if getattr(settings, "MASTERGO_MOCK_OTP", False) and code == str(settings.MASTERGO_MOCK_OTP_CODE):
        cache.delete(phone_key)
        return _finalize_login(phone, full_name=full_name, language=language)

    entry = cache.get(phone_key)
    if not entry:
        raise OTPError("otp_expired")

    attempts_left = int(entry.get("attempts_left") or 0)
    if attempts_left <= 0:
        cache.delete(phone_key)
        raise OTPError("otp_expired")

    attempts_left -= 1
    entry["attempts_left"] = attempts_left
    cache.set(phone_key, entry, timeout=OTP_TTL_SECONDS)

    if not check_password(code, entry.get("code_hash", "")):
        if attempts_left <= 0:
            cache.delete(phone_key)
            raise OTPError("otp_expired")
        raise OTPError("otp_mismatch", attempts_left=attempts_left)

    cache.delete(phone_key)
    return _finalize_login(phone, full_name=full_name, language=language)


def _finalize_login(phone: str, *, full_name: str = "", language: str | None = None) -> OTPVerifyResult:
    user, is_new_user = User.objects.get_or_create(phone=phone)
    update_fields = ["updated_at"]
    if full_name:
        user.full_name = full_name
        update_fields.append("full_name")
    if language:
        user.language = language
        update_fields.append("language")
    user.save(update_fields=update_fields)

    # TEST MODE: auto-approve every new user as a master (with a test package) so
    # they can go online without moderation. Toggle with MASTERGO_AUTO_APPROVE_MASTERS.
    if getattr(settings, "MASTERGO_AUTO_APPROVE_MASTERS", False):
        from apps.masters.services import auto_approve_master, get_or_create_master_profile

        auto_approve_master(get_or_create_master_profile(user))
        user.refresh_from_db()

    return OTPVerifyResult(user=user, is_new_user=is_new_user)


class AccountDeletionError(Exception):
    """Raised when an account can't be deleted right now."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# Mirrors the "master is engaged" statuses used elsewhere (see
# apps.masters.services.master_has_blocking_order) -- an order in one of
# these means a real person (the other party) is actively depending on this
# account, so deleting it out from under them would strand their job.
_NON_DELETABLE_ORDER_STATUSES = [
    "accepted_by_master",
    "price_proposed",
    "price_accepted",
    "master_on_way",
    "master_arrived",
    "in_progress",
    "work_done",
    "disputed",
]


def user_has_active_order(user: User) -> bool:
    from apps.orders.models import Order

    if Order.objects.filter(client=user, status__in=_NON_DELETABLE_ORDER_STATUSES).exists():
        return True
    master_profile = getattr(user, "master_profile", None)
    if master_profile is not None and master_profile.orders.filter(
        status__in=_NON_DELETABLE_ORDER_STATUSES
    ).exists():
        return True
    return False


def delete_account(user: User) -> None:
    """Anonymize and deactivate the account (Google Play deletion requirement).

    The row itself is kept rather than hard-deleted: Order.client uses
    on_delete=PROTECT, so a user with order history can't be removed without
    breaking that history. Instead we anonymize personal data, free up the
    phone number, and deactivate the account so it can never log in again.
    """
    if user_has_active_order(user):
        raise AccountDeletionError("active_order_exists")

    import uuid

    from rest_framework.authtoken.models import Token

    user.phone = f"deleted:{user.pk}:{uuid.uuid4().hex[:8]}"
    user.full_name = ""
    user.first_name = ""
    user.last_name = ""
    user.birth_date = None
    user.avatar = None
    user.avatar_url = ""
    user.pinfl = ""
    user.myid_verified_at = None
    user.is_active = False
    user.set_unusable_password()
    user.save()
    Token.objects.filter(user=user).delete()


def get_or_create_client(phone: str, full_name: str = "") -> User:
    user, _ = User.objects.get_or_create(phone=phone, defaults={"full_name": full_name})
    if full_name and user.full_name != full_name:
        user.full_name = full_name
        user.save(update_fields=["full_name", "updated_at"])
    return user


def _phone_key(phone: str, purpose: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return f"otp:{purpose}:{digits or phone}"

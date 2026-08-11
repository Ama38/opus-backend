from dataclasses import dataclass
from datetime import timedelta
import logging
import random

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.utils import timezone

from .models import OTPPurpose, User
from .sms import SmsError, send_otp_sms

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

    if getattr(settings, "MASTERGO_MOCK_OTP", False):
        code = str(settings.MASTERGO_MOCK_OTP_CODE)
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

    # In mock mode the code is a fixed well-known value, so no SMS is needed.
    # Otherwise deliver it: real gateway in production, console in dry-run/dev.
    if not getattr(settings, "MASTERGO_MOCK_OTP", False):
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


def get_or_create_client(phone: str, full_name: str = "") -> User:
    user, _ = User.objects.get_or_create(phone=phone, defaults={"full_name": full_name})
    if full_name and user.full_name != full_name:
        user.full_name = full_name
        user.save(update_fields=["full_name", "updated_at"])
    return user


def _phone_key(phone: str, purpose: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return f"otp:{purpose}:{digits or phone}"

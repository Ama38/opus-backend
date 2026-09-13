from django.conf import settings
from django.contrib.auth import login, logout
from django.utils import timezone
from rest_framework import permissions, response, status, views
from rest_framework.authtoken.models import Token

from . import myid
from .models import OTPPurpose
from .serializers import (
    MockOTPStartSerializer,
    MockOTPVerifySerializer,
    MyIdVerifySerializer,
    PasswordLoginSerializer,
    UserSerializer,
)
from .services import OTPError, start_otp, verify_otp


class MockOTPStartView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = MockOTPStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = start_otp(serializer.validated_data["phone"], OTPPurpose.LOGIN)
        except OTPError as error:
            return _otp_error_response(error)
        return response.Response({"sent": True, "expires_at": result.expires_at})


class MockOTPVerifyView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = MockOTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = verify_otp(
                phone=serializer.validated_data["phone"],
                code=serializer.validated_data["code"],
                full_name=serializer.validated_data.get("full_name", ""),
                language=serializer.validated_data.get("language"),
            )
        except OTPError as error:
            return _otp_error_response(error)
        user = result.user
        login(request, user)
        token, _ = Token.objects.get_or_create(user=user)
        return response.Response(
            {
                "user": UserSerializer(user, context={"request": request}).data,
                "token": token.key,
                "is_new_user": result.is_new_user,
            }
        )


class PasswordLoginView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        login(request, serializer.validated_data["user"])
        token, _ = Token.objects.get_or_create(user=serializer.validated_data["user"])
        return response.Response(
            {
                "user": UserSerializer(serializer.validated_data["user"], context={"request": request}).data,
                "token": token.key,
            }
        )


class LogoutView(views.APIView):
    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        logout(request)
        return response.Response({"status": "ok"})


class MeView(views.APIView):
    def get(self, request):
        return response.Response(
            {"user": UserSerializer(request.user, context={"request": request}).data}
        )

    def patch(self, request):
        serializer = UserSerializer(
            request.user, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_at=timezone.now())
        return response.Response({"user": serializer.data})


class MyIdSessionView(views.APIView):
    """Create a MyID identification session for the mobile SDK to launch."""

    def post(self, request):
        try:
            session_id = myid.create_session(phone_number=request.user.phone)
        except myid.MyIdError as error:
            return response.Response({"code": str(error)}, status=status.HTTP_502_BAD_GATEWAY)

        return response.Response(
            {
                "session_id": session_id,
                "client_hash": settings.MYID_CLIENT_HASH,
                "client_hash_id": settings.MYID_CLIENT_HASH_ID,
                "environment": settings.MYID_ENVIRONMENT,
            }
        )


class MyIdVerifyView(views.APIView):
    """Exchange the SDK's one-time code for verified ФИО and save them."""

    def post(self, request):
        serializer = MyIdVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            data = myid.get_identification_data(serializer.validated_data["code"])
        except myid.MyIdError as error:
            return response.Response({"code": str(error)}, status=status.HTTP_502_BAD_GATEWAY)

        common_data = ((data.get("data") or {}).get("profile") or {}).get("common_data") or {}
        first_name = common_data.get("first_name", "")
        last_name = common_data.get("last_name", "")
        if not first_name or not last_name:
            return response.Response({"code": "myid_profile_incomplete"}, status=status.HTTP_502_BAD_GATEWAY)

        user = request.user
        user.first_name = first_name
        user.last_name = last_name
        user.full_name = f"{first_name} {last_name}".strip()
        birth_date = common_data.get("birth_date")
        if birth_date:
            user.birth_date = birth_date
        pinfl = common_data.get("pinfl")
        if pinfl:
            user.pinfl = pinfl
        user.myid_verified_at = timezone.now()
        user.save(
            update_fields=[
                "first_name",
                "last_name",
                "full_name",
                "birth_date",
                "pinfl",
                "myid_verified_at",
                "updated_at",
            ]
        )
        return response.Response({"user": UserSerializer(user, context={"request": request}).data})


def _otp_error_response(error: OTPError):
    payload = {"code": error.code}
    if error.attempts_left is not None:
        payload["attempts_left"] = error.attempts_left
    if error.retry_after is not None:
        payload["retry_after"] = error.retry_after
    if error.code in {"otp_expired"}:
        http_status = status.HTTP_410_GONE
    elif error.code in {"otp_mismatch"}:
        http_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    elif error.code in {"otp_throttled", "otp_hourly_limit"}:
        http_status = status.HTTP_429_TOO_MANY_REQUESTS
    elif error.code in {"otp_send_failed"}:
        http_status = status.HTTP_502_BAD_GATEWAY
    else:
        http_status = status.HTTP_400_BAD_REQUEST
    return response.Response(payload, status=http_status)

from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import DeviceToken, NotificationEvent
from .serializers import DeviceTokenSerializer, NotificationEventSerializer


class NotificationEventViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationEventSerializer

    def get_queryset(self):
        queryset = NotificationEvent.objects.select_related("user")
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(user=self.request.user)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def register_device_token(request):
    """Register (or refresh) the caller's FCM token for push delivery."""
    serializer = DeviceTokenSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    token = serializer.validated_data["token"]
    platform = serializer.validated_data["platform"]

    # A token identifies one device: move it to the current user and reactivate.
    DeviceToken.objects.update_or_create(
        token=token,
        defaults={"user": request.user, "platform": platform, "is_active": True},
    )
    return Response({"status": "ok"}, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def unregister_device_token(request):
    """Deactivate a token (e.g. on logout)."""
    token = request.data.get("token", "")
    if token:
        DeviceToken.objects.filter(token=token, user=request.user).update(is_active=False)
    return Response({"status": "ok"}, status=status.HTTP_200_OK)

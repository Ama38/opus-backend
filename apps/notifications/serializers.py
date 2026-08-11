from rest_framework import serializers

from .models import DevicePlatform, NotificationEvent


class NotificationEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationEvent
        fields = ["id", "channel", "event_type", "title", "body", "payload", "status", "created_at", "sent_at"]


class DeviceTokenSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=255)
    platform = serializers.ChoiceField(
        choices=DevicePlatform.choices, default=DevicePlatform.ANDROID
    )


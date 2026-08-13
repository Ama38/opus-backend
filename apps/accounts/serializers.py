from django.contrib.auth import authenticate
from rest_framework import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    avatar = serializers.ImageField(write_only=True, required=False)
    avatar_url = serializers.SerializerMethodField()
    is_master_online = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "phone",
            "full_name",
            "first_name",
            "last_name",
            "birth_date",
            "avatar",
            "avatar_url",
            "language",
            "is_client_enabled",
            "is_master_enabled",
            "is_master_online",
        ]
        read_only_fields = ["id", "is_client_enabled", "is_master_enabled", "is_master_online"]

    def get_is_master_online(self, obj) -> bool:
        profile = getattr(obj, "master_profile", None)
        return bool(profile and profile.is_online)

    def get_avatar_url(self, obj) -> str:
        if obj.avatar:
            url = obj.avatar.url
            request = self.context.get("request")
            return request.build_absolute_uri(url) if request else url
        return obj.avatar_url or ""

    def update(self, instance, validated_data):
        # Keep full_name in sync when the parts are edited (used across the UI).
        instance = super().update(instance, validated_data)
        parts = [instance.first_name.strip(), instance.last_name.strip()]
        composed = " ".join(part for part in parts if part)
        if composed and composed != instance.full_name:
            instance.full_name = composed
            instance.save(update_fields=["full_name"])
        return instance


class MockOTPStartSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=32)


class MockOTPVerifySerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=32)
    code = serializers.CharField(min_length=4, max_length=4)
    full_name = serializers.CharField(max_length=160, required=False, allow_blank=True)
    language = serializers.ChoiceField(choices=User._meta.get_field("language").choices, required=False)


class PasswordLoginSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=32)
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(username=attrs["phone"], password=attrs["password"])
        if user is None:
            raise serializers.ValidationError({"code": "invalid_credentials"})
        attrs["user"] = user
        return attrs

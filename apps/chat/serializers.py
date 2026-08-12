from rest_framework import serializers

from .models import ChatMessage, ChatRoom, MessageKind


class ChatMessageSerializer(serializers.ModelSerializer):
    sender_phone = serializers.CharField(source="sender.phone", read_only=True)
    sender_name = serializers.CharField(source="sender.full_name", read_only=True)

    class Meta:
        model = ChatMessage
        fields = [
            "id",
            "room",
            "sender",
            "sender_phone",
            "sender_name",
            "kind",
            "text",
            "attachment_url",
            "price_uzs",
            "created_at",
            "read_at",
        ]
        read_only_fields = ["id", "sender", "sender_phone", "sender_name", "created_at", "read_at"]


class ChatRoomSerializer(serializers.ModelSerializer):
    messages = ChatMessageSerializer(many=True, read_only=True)

    class Meta:
        model = ChatRoom
        fields = ["id", "order", "created_at", "closed_at", "messages"]
        read_only_fields = ["id", "created_at", "closed_at", "messages"]


class ChatMessageCreateSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=[MessageKind.TEXT, MessageKind.PHOTO, MessageKind.VIDEO], default=MessageKind.TEXT)
    text = serializers.CharField(required=False, allow_blank=True)
    attachment_url = serializers.URLField(required=False, allow_blank=True)
    # Correlates the sender's optimistic UI item with the persisted message.
    # It is intentionally transient: the server UUID remains the source of truth.
    client_message_id = serializers.CharField(required=False, allow_blank=True, max_length=80)
    # Direct multipart upload of the photo/video; the view stores it and fills
    # attachment_url from the saved file.
    attachment = serializers.FileField(required=False, write_only=True)

    def validate(self, attrs):
        if attrs["kind"] == MessageKind.TEXT and not attrs.get("text", "").strip():
            raise serializers.ValidationError({"text": "required_for_text_message"})
        if attrs["kind"] in {MessageKind.PHOTO, MessageKind.VIDEO} and not (
            attrs.get("attachment_url") or attrs.get("attachment")
        ):
            raise serializers.ValidationError({"attachment": "required_for_media_message"})
        return attrs


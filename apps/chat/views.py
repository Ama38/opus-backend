import json
from uuid import uuid4

from django.conf import settings
from django.core.files.storage import default_storage
from rest_framework import decorators, response, status, viewsets
from rest_framework.renderers import JSONRenderer

from apps.orders.models import Order, OrderStatus
from apps.notifications.realtime import safe_group_send

from .models import ChatMessage, ChatRoom
from .serializers import ChatMessageCreateSerializer, ChatMessageSerializer, ChatRoomSerializer
from .services import get_or_create_order_room


class ChatRoomViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ChatRoomSerializer

    def get_queryset(self):
        queryset = ChatRoom.objects.select_related("order__client", "order__master__user").prefetch_related("messages__sender")
        if self.request.user.is_staff:
            return queryset
        return (queryset.filter(order__client=self.request.user) | queryset.filter(order__master__user=self.request.user)).distinct()

    @decorators.action(detail=False, methods=["post"], url_path="for-order")
    def for_order(self, request):
        order_id = request.data.get("order_id")
        order = Order.objects.filter(id=order_id).first()
        if order is None:
            return response.Response({"code": "order_not_found"}, status=status.HTTP_404_NOT_FOUND)
        if not request.user.is_staff and order.client_id != request.user.id and getattr(order.master, "user_id", None) != request.user.id:
            return response.Response({"code": "forbidden"}, status=status.HTTP_403_FORBIDDEN)
        if order.master_id is None or order.status in {
            OrderStatus.DRAFT,
            OrderStatus.SEARCHING,
            OrderStatus.OFFERED_TO_MASTER,
        }:
            return response.Response(
                {"code": "chat_not_available"}, status=status.HTTP_409_CONFLICT
            )
        room = get_or_create_order_room(order)
        return response.Response({"room": self.get_serializer(room).data})


class ChatMessageViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ChatMessageSerializer

    def get_queryset(self):
        queryset = ChatMessage.objects.select_related("room__order__client", "room__order__master__user", "sender")
        if self.request.user.is_staff:
            return queryset
        return (queryset.filter(room__order__client=self.request.user) | queryset.filter(room__order__master__user=self.request.user)).distinct()

    @decorators.action(detail=False, methods=["post"])
    def send(self, request):
        room = ChatRoom.objects.filter(id=request.data.get("room_id")).select_related("order__client", "order__master__user").first()
        if room is None:
            return response.Response({"code": "room_not_found"}, status=status.HTTP_404_NOT_FOUND)
        if not request.user.is_staff and room.order.client_id != request.user.id and getattr(room.order.master, "user_id", None) != request.user.id:
            return response.Response({"code": "forbidden"}, status=status.HTTP_403_FORBIDDEN)
        if room.closed_at is not None or room.order.status in {
            OrderStatus.COMPLETED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
        }:
            return response.Response(
                {"code": "chat_closed"}, status=status.HTTP_409_CONFLICT
            )
        serializer = ChatMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        attachment_url = serializer.validated_data.get("attachment_url", "")
        upload = serializer.validated_data.get("attachment")
        if upload is not None:
            safe_name = f"{uuid4().hex}_{upload.name}".replace(" ", "_")
            saved_path = default_storage.save(f"chat/{room.id}/{safe_name}", upload)
            attachment_url = request.build_absolute_uri(settings.MEDIA_URL + saved_path)
        message = ChatMessage.objects.create(
            room=room,
            sender=request.user,
            kind=serializer.validated_data["kind"],
            text=serializer.validated_data.get("text", ""),
            attachment_url=attachment_url,
        )
        payload = json.loads(JSONRenderer().render(self.get_serializer(message).data))
        client_message_id = serializer.validated_data.get("client_message_id", "")
        if client_message_id:
            payload["client_message_id"] = client_message_id
        safe_group_send(
            f"chat_{room.id}",
            {"type": "chat.message", "payload": {"event": "message.created", "message": payload}},
        )
        return response.Response({"message": payload}, status=status.HTTP_201_CREATED)

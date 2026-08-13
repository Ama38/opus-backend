from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from rest_framework.authtoken.models import Token

from apps.chat.services import get_or_create_order_room
from apps.masters.models import MasterProfile

from .models import MasterOffer, MasterOfferStatus
from .serializers import MasterOfferSerializer, OrderSerializer
from .services import (
    OrderActionError,
    accept_master_offer,
    decline_master_offer,
    expire_master_offer,
    expire_stale_master_offers,
)


class OrderConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.order_id = self.scope["url_route"]["kwargs"]["order_id"]
        self.group_name = f"order_{self.order_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def order_event(self, event):
        await self.send_json(event["payload"])


class MasterOrdersConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user = await self._user_from_token()
        if self.user is None:
            await self.close(code=4401)
            return

        self.group_name = f"master_user_{self.user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        # Finish the handshake first, then reconcile only this master's offers.
        # Global reconciliation here used to delay every reconnect and multiply
        # matching work when several phones reconnected together.
        await self._reconcile_stale_offers()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def order_event(self, event):
        await self.send_json(event["payload"])

    async def receive_json(self, content, **kwargs):
        action = content.get("action") or content.get("type")
        if action == "accept_offer":
            await self.send_json(await self._accept_offer(content))
            return
        if action == "decline_offer":
            await self.send_json(await self._decline_offer(content))
            return
        await self.send_json({"event": "offer_action_error", "code": "unknown_action"})

    @database_sync_to_async
    def _user_from_token(self):
        query_string = self.scope.get("query_string", b"").decode()
        token = ""
        for part in query_string.split("&"):
            if part.startswith("token="):
                token = part.split("=", 1)[1]
                break
        if not token:
            return None
        auth_token = Token.objects.select_related("user").filter(key=token).first()
        return auth_token.user if auth_token else None

    @database_sync_to_async
    def _reconcile_stale_offers(self):
        profile = MasterProfile.objects.filter(user=self.user).first()
        if profile is None:
            return 0
        return expire_stale_master_offers(
            master=profile,
            continue_matching=True,
            limit=10,
        )

    @database_sync_to_async
    def _accept_offer(self, payload: dict) -> dict:
        offer = self._get_pending_offer(payload)
        if isinstance(offer, dict):
            return offer
        try:
            order = accept_master_offer(offer)
        except OrderActionError as error:
            if error.code == "offer_expired":
                expire_master_offer(offer.id)
            return {"event": "offer_action_error", "code": error.code, "offer_id": offer.id}
        get_or_create_order_room(order)
        return {"event": "offer_accepted", "offer_id": offer.id, "order": OrderSerializer(order).data}

    @database_sync_to_async
    def _decline_offer(self, payload: dict) -> dict:
        offer = self._get_pending_offer(payload)
        if isinstance(offer, dict):
            return offer
        try:
            result = decline_master_offer(offer, actor=self.user)
        except OrderActionError as error:
            if error.code == "offer_expired":
                expire_master_offer(offer.id)
            return {"event": "offer_action_error", "code": error.code, "offer_id": offer.id}
        return {
            "event": "offer_declined",
            "offer_id": offer.id,
            "code": "no_master_found" if result.exhausted else "matched",
            "attempted_radii_km": list(result.attempted_radii_km),
            "next_offer": MasterOfferSerializer(result.offer).data if result.offer else None,
            "order": OrderSerializer(result.order).data,
        }

    def _get_pending_offer(self, payload: dict):
        profile = MasterProfile.objects.filter(user=self.user).first()
        if profile is None:
            return {"event": "offer_action_error", "code": "master_profile_required"}
        queryset = MasterOffer.objects.select_related("order", "master__user").filter(
            master=profile,
            status=MasterOfferStatus.PENDING,
        )
        offer_id = payload.get("offer_id")
        order_id = payload.get("order_id")
        if offer_id:
            queryset = queryset.filter(id=offer_id)
        elif order_id:
            queryset = queryset.filter(order_id=order_id)
        else:
            return {"event": "offer_action_error", "code": "offer_id_required"}
        offer = queryset.order_by("-created_at").first()
        if offer is None:
            return {"event": "offer_action_error", "code": "offer_not_found"}
        return offer

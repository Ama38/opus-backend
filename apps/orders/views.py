from django.db import transaction
from rest_framework import decorators, response, status, viewsets

from apps.chat.services import get_or_create_order_room
from apps.masters.models import MasterProfile

from .models import MasterOfferStatus, Order, OrderAttachment, OrderCancelReason, OrderStatus, PriceProposalStatus
from .serializers import (
    MatchSerializer,
    MasterOfferSerializer,
    OrderCancelSerializer,
    OrderCompleteSerializer,
    OrderSerializer,
    OrderStatusUpdateSerializer,
    PriceProposalCreateSerializer,
    PriceProposalSerializer,
)
from .services import (
    OrderActionError,
    accept_master_offer,
    accept_price,
    bargain_price,
    client_reject_master,
    decline_master_offer,
    expire_master_offer,
    match_open_orders,
    match_order_with_radius_expansion,
    propose_price,
    reject_price,
    transition_order,
)


class OrderViewSet(viewsets.ModelViewSet):
    serializer_class = OrderSerializer

    def _forbidden(self, code: str):
        return response.Response({"code": code}, status=status.HTTP_403_FORBIDDEN)

    def _require_client(self, request, order: Order):
        if order.client_id == request.user.id:
            return None
        return self._forbidden("client_only_action")

    def _get_master_profile(self, request):
        return MasterProfile.objects.filter(user=request.user).first()

    def _require_assigned_master(self, request, order: Order):
        profile = self._get_master_profile(request)
        if profile is None or order.master_id != profile.id:
            return None, self._forbidden("assigned_master_only_action")
        return profile, None

    def get_queryset(self):
        queryset = (
            Order.objects.select_related("client", "master__user", "category")
            .prefetch_related("attachments", "master_offers", "price_proposals")
        )
        if self.request.user.is_staff:
            return queryset
        return (queryset.filter(client=self.request.user) | queryset.filter(master__user=self.request.user)).distinct()

    def perform_create(self, serializer):
        order = serializer.save(client=self.request.user)
        attachments = self.request.FILES.getlist("attachments[]") or self.request.FILES.getlist("attachments")
        for attachment in attachments:
            OrderAttachment.objects.create(order=order, file=attachment)
        get_or_create_order_room(order)
        match_order_with_radius_expansion(order)

    @decorators.action(detail=True, methods=["post"])
    def match(self, request, pk=None):
        order = self.get_object()
        denied = self._require_client(request, order)
        if denied is not None:
            return denied
        serializer = MatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = match_order_with_radius_expansion(order, serializer.validated_data["radius_km"])
        return response.Response(
            {
                "code": "no_master_found" if result.exhausted else "matched",
                "attempted_radii_km": list(result.attempted_radii_km),
                "offer": MasterOfferSerializer(result.offer).data if result.offer else None,
                "order": OrderSerializer(result.order).data,
            }
        )

    @decorators.action(detail=True, methods=["post"], url_path="master-accept")
    def master_accept(self, request, pk=None):
        order = self.get_object()
        profile = self._get_master_profile(request)
        if profile is None:
            return self._forbidden("master_profile_required")
        offer = order.master_offers.filter(master=profile, status=MasterOfferStatus.PENDING).order_by("-created_at").first()
        if offer is None:
            return response.Response({"code": "offer_not_found"}, status=status.HTTP_404_NOT_FOUND)
        try:
            order = accept_master_offer(offer)
        except OrderActionError as error:
            if error.code == "offer_expired":
                expire_master_offer(offer.id)
            return response.Response({"code": error.code}, status=status.HTTP_400_BAD_REQUEST)
        get_or_create_order_room(order)
        return response.Response({"order": OrderSerializer(order).data})

    @decorators.action(detail=True, methods=["post"], url_path="master-decline")
    def master_decline(self, request, pk=None):
        order = self.get_object()
        profile = self._get_master_profile(request)
        if profile is None:
            return self._forbidden("master_profile_required")
        offer = order.master_offers.filter(master=profile, status=MasterOfferStatus.PENDING).order_by("-created_at").first()
        if offer is None:
            return response.Response({"code": "offer_not_found"}, status=status.HTTP_404_NOT_FOUND)
        try:
            result = decline_master_offer(offer, actor=request.user)
        except OrderActionError as error:
            if error.code == "offer_expired":
                expire_master_offer(offer.id)
            return response.Response({"code": error.code}, status=status.HTTP_400_BAD_REQUEST)
        return response.Response(
            {
                "code": "no_master_found" if result.exhausted else "matched",
                "attempted_radii_km": list(result.attempted_radii_km),
                "offer": MasterOfferSerializer(result.offer).data if result.offer else None,
                "order": OrderSerializer(result.order).data,
            }
        )

    @decorators.action(detail=True, methods=["post"], url_path="propose-price")
    def create_price_proposal(self, request, pk=None):
        order = self.get_object()
        profile, denied = self._require_assigned_master(request, order)
        if denied is not None:
            return denied
        serializer = PriceProposalCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        proposal = propose_price(order, profile, serializer.validated_data["amount_uzs"])
        return response.Response({"proposal": PriceProposalSerializer(proposal).data, "order": OrderSerializer(order).data})

    @decorators.action(detail=True, methods=["post"], url_path="accept-price")
    def accept_latest_price(self, request, pk=None):
        order = self.get_object()
        denied = self._require_client(request, order)
        if denied is not None:
            return denied
        proposal = order.price_proposals.filter(status=PriceProposalStatus.PENDING).order_by("-created_at").first()
        if proposal is None:
            return response.Response({"code": "price_proposal_not_found"}, status=status.HTTP_404_NOT_FOUND)
        order = accept_price(proposal, request.user)
        return response.Response({"order": OrderSerializer(order).data})

    @decorators.action(detail=True, methods=["post"], url_path="reject-master")
    def reject_master(self, request, pk=None):
        order = self.get_object()
        denied = self._require_client(request, order)
        if denied is not None:
            return denied
        try:
            result = client_reject_master(order, actor=request.user)
        except OrderActionError as error:
            return response.Response({"code": error.code}, status=status.HTTP_400_BAD_REQUEST)
        return response.Response(
            {
                "code": "operator_search" if result.exhausted else "rematching",
                "needs_operator": result.order.needs_operator,
                "order": OrderSerializer(result.order).data,
            }
        )

    @decorators.action(detail=True, methods=["post"], url_path="reject-price")
    def reject_latest_price(self, request, pk=None):
        order = self.get_object()
        denied = self._require_client(request, order)
        if denied is not None:
            return denied
        proposal = order.price_proposals.filter(status=PriceProposalStatus.PENDING).order_by("-created_at").first()
        if proposal is None:
            return response.Response({"code": "price_proposal_not_found"}, status=status.HTTP_404_NOT_FOUND)
        order = reject_price(proposal, request.user)
        match_open_orders()
        return response.Response({"order": OrderSerializer(order).data})

    @decorators.action(detail=True, methods=["post"], url_path="bargain-price")
    def bargain_latest_price(self, request, pk=None):
        """Client wants to negotiate the proposed price ("Торг"): keep the order
        and drop it back to chat so the master can propose a new price."""
        order = self.get_object()
        denied = self._require_client(request, order)
        if denied is not None:
            return denied
        proposal = order.price_proposals.filter(status=PriceProposalStatus.PENDING).order_by("-created_at").first()
        if proposal is None:
            return response.Response({"code": "price_proposal_not_found"}, status=status.HTTP_404_NOT_FOUND)
        order = bargain_price(proposal, request.user)
        return response.Response({"order": OrderSerializer(order).data})

    @decorators.action(detail=True, methods=["post"], url_path="status")
    def update_status(self, request, pk=None):
        order = self.get_object()
        _, denied = self._require_assigned_master(request, order)
        if denied is not None:
            return denied
        serializer = OrderStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = transition_order(
            order,
            serializer.validated_data["status"],
            actor=request.user,
            reason=serializer.validated_data.get("reason", "manual_status_update"),
        )
        return response.Response({"order": OrderSerializer(order).data})

    @decorators.action(detail=True, methods=["get"], url_path="track")
    def track(self, request, pk=None):
        """The master's recorded route for this order (TZ §6.1), oldest first —
        used to draw the travelled path on the completed order screen."""
        order = self.get_object()
        pings = order.master_pings.order_by("created_at").values(
            "latitude", "longitude", "created_at"
        )
        points = [
            {
                "latitude": float(p["latitude"]),
                "longitude": float(p["longitude"]),
                "created_at": p["created_at"].isoformat(),
            }
            for p in pings
        ]
        return response.Response({"points": points, "count": len(points)})

    @decorators.action(detail=True, methods=["post"], url_path="onsite-price")
    def onsite_price(self, request, pk=None):
        """Master fixes the exact price on site after inspecting the job (TZ §5.4).
        Updates the agreed price so the client sees it before work starts."""
        order = self.get_object()
        _, denied = self._require_assigned_master(request, order)
        if denied is not None:
            return denied
        raw_price = request.data.get("agreed_price_uzs")
        try:
            amount = int(raw_price)
        except (TypeError, ValueError):
            return response.Response({"code": "invalid_price"}, status=status.HTTP_400_BAD_REQUEST)
        if amount <= 0:
            return response.Response({"code": "invalid_price"}, status=status.HTTP_400_BAD_REQUEST)
        order.agreed_price_uzs = amount
        order.save(update_fields=["agreed_price_uzs", "updated_at"])
        return response.Response({"order": OrderSerializer(order).data})

    @decorators.action(detail=True, methods=["post"], url_path="work-report")
    def work_report(self, request, pk=None):
        """Master submits the result of the job: photos of the finished work and
        the actual amount charged. Stored before the client confirms, so the
        completion photos and declared sum are persisted (TZ §6.3)."""
        order = self.get_object()
        _, denied = self._require_assigned_master(request, order)
        if denied is not None:
            return denied
        files = request.FILES.getlist("attachments[]") or request.FILES.getlist("attachments")
        for attachment in files:
            OrderAttachment.objects.create(order=order, file=attachment)
        update_fields = ["updated_at"]
        raw_price = request.data.get("final_price_uzs")
        if raw_price not in (None, ""):
            try:
                order.final_price_uzs = int(raw_price)
                update_fields.append("final_price_uzs")
            except (TypeError, ValueError):
                return response.Response(
                    {"code": "invalid_final_price"}, status=status.HTTP_400_BAD_REQUEST
                )
        order.save(update_fields=update_fields)
        return response.Response(
            {"order": OrderSerializer(order).data, "attachments_added": len(files)}
        )

    @decorators.action(detail=True, methods=["post"])
    @transaction.atomic
    def complete(self, request, pk=None):
        order = self.get_object()
        denied = self._require_client(request, order)
        if denied is not None:
            return denied
        serializer = OrderCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if order.status != OrderStatus.WORK_DONE:
            return response.Response({"code": "order_not_ready_for_completion"}, status=status.HTTP_400_BAD_REQUEST)
        order.final_price_uzs = serializer.validated_data["final_price_uzs"]
        order.payment_method = serializer.validated_data["payment_method"]
        # Flag big divergence from the agreed price for operator review (>30%).
        agreed = order.agreed_price_uzs
        final = order.final_price_uzs
        divergence_flagged = False
        if agreed and final and abs(final - agreed) / agreed > 0.30:
            order.needs_operator = True
            divergence_flagged = True
        order.save(update_fields=["final_price_uzs", "payment_method", "needs_operator", "updated_at"])
        # The order slot is already debited at acceptance (subscription model);
        # nothing is charged again on completion.
        if order.status == OrderStatus.WORK_DONE:
            order = transition_order(
                order,
                OrderStatus.COMPLETED,
                actor=request.user,
                reason="client_confirmed",
                metadata={"price_divergence_flagged": divergence_flagged},
            )
        match_open_orders()
        return response.Response({"order": OrderSerializer(order).data})

    @decorators.action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        denied = self._require_client(request, order)
        if denied is not None:
            return denied
        serializer = OrderCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order.cancellation_reason = serializer.validated_data.get("reason") or OrderCancelReason.CLIENT_CANCELLED
        order.cancellation_comment = serializer.validated_data.get("comment", "")
        order.save(update_fields=["cancellation_reason", "cancellation_comment", "updated_at"])
        order = transition_order(
            order,
            OrderStatus.CANCELLED,
            actor=request.user,
            reason=order.cancellation_reason,
            metadata={"comment": order.cancellation_comment} if order.cancellation_comment else {},
        )
        match_open_orders()
        return response.Response({"order": OrderSerializer(order).data})

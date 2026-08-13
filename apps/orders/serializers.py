from rest_framework import serializers

from apps.masters.models import MasterProfile, ServiceCategory
from apps.masters.serializers import ServiceCategorySerializer

from .models import (
    MasterOffer,
    MasterOfferStatus,
    Order,
    OrderAttachment,
    OrderCancelReason,
    OrderStatus,
    PriceProposal,
    PriceProposalStatus,
)


class OrderAttachmentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = OrderAttachment
        fields = ["id", "url", "uploaded_at"]
        read_only_fields = fields

    def get_url(self, attachment: OrderAttachment) -> str:
        if not attachment.file:
            return ""
        request = self.context.get("request")
        url = attachment.file.url
        return request.build_absolute_uri(url) if request else url


class OrderSerializer(serializers.ModelSerializer):
    category = ServiceCategorySerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=ServiceCategory.objects.filter(is_active=True),
        source="category",
        write_only=True,
    )
    # Optional: the master the client picked from the directory (direct offer).
    preferred_master_id = serializers.PrimaryKeyRelatedField(
        queryset=MasterProfile.objects.all(),
        source="preferred_master",
        write_only=True,
        required=False,
        allow_null=True,
    )
    client_phone = serializers.CharField(source="client.phone", read_only=True)
    master_id = serializers.IntegerField(source="master.id", read_only=True)
    master_name = serializers.CharField(source="master.user.full_name", read_only=True)
    master_phone = serializers.CharField(source="master.user.phone", read_only=True)
    pending_price_proposal_uzs = serializers.SerializerMethodField()
    pending_master_offer_id = serializers.SerializerMethodField()
    pending_master_offer_expires_at = serializers.SerializerMethodField()
    attachments = OrderAttachmentSerializer(many=True, read_only=True)
    description = serializers.CharField(min_length=10, allow_blank=False)
    scheduled_at = serializers.DateTimeField(required=False, allow_null=True)
    budget_ceiling_uzs = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=50_000,
        max_value=10_000_000,
    )

    class Meta:
        model = Order
        fields = [
            "id",
            "client_phone",
            "master_id",
            "master_name",
            "master_phone",
            "category",
            "category_id",
            "preferred_master_id",
            "status",
            "description",
            "address_text",
            "latitude",
            "longitude",
            "scheduled_at",
            "budget_ceiling_uzs",
            "price_flexible",
            "needs_operator",
            "attachments",
            "agreed_price_uzs",
            "pending_price_proposal_uzs",
            "pending_master_offer_id",
            "pending_master_offer_expires_at",
            "final_price_uzs",
            "payment_method",
            "cancellation_reason",
            "cancellation_comment",
            "created_at",
            "updated_at",
            "completed_at",
        ]
        read_only_fields = [
            "id",
            "client_phone",
            "master_id",
            "master_name",
            "master_phone",
            "needs_operator",
            "status",
            "agreed_price_uzs",
            "pending_price_proposal_uzs",
            "pending_master_offer_id",
            "pending_master_offer_expires_at",
            "final_price_uzs",
            "payment_method",
            "cancellation_reason",
            "cancellation_comment",
            "created_at",
            "updated_at",
            "completed_at",
        ]

    def get_pending_price_proposal_uzs(self, order: Order):
        proposal = order.price_proposals.filter(status=PriceProposalStatus.PENDING).order_by("-created_at").first()
        return proposal.amount_uzs if proposal else None

    def _pending_master_offer(self, order: Order):
        return order.master_offers.filter(status=MasterOfferStatus.PENDING).order_by("-created_at").first()

    def get_pending_master_offer_id(self, order: Order):
        offer = self._pending_master_offer(order)
        return offer.id if offer else None

    def get_pending_master_offer_expires_at(self, order: Order):
        offer = self._pending_master_offer(order)
        return offer.expires_at.isoformat() if offer else None


class MasterOfferSerializer(serializers.ModelSerializer):
    master_name = serializers.CharField(source="master.user.full_name", read_only=True)
    master_phone = serializers.CharField(source="master.user.phone", read_only=True)

    class Meta:
        model = MasterOffer
        fields = ["id", "order", "master", "master_name", "master_phone", "status", "score", "radius_km", "expires_at", "created_at"]
        read_only_fields = fields


class PriceProposalSerializer(serializers.ModelSerializer):
    master_name = serializers.CharField(source="master.user.full_name", read_only=True)

    class Meta:
        model = PriceProposal
        fields = ["id", "order", "master", "master_name", "amount_uzs", "status", "created_at", "responded_at"]
        read_only_fields = fields


class MatchSerializer(serializers.Serializer):
    from django.conf import settings as _settings

    _radii = list(getattr(_settings, "MASTERGO_MATCHING_RADII_KM", (1, 3, 6)))
    radius_km = serializers.ChoiceField(choices=_radii, default=_radii[0])


class PriceProposalCreateSerializer(serializers.Serializer):
    amount_uzs = serializers.IntegerField(min_value=1)


class OrderStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            OrderStatus.MASTER_ON_WAY,
            OrderStatus.MASTER_ARRIVED,
            OrderStatus.IN_PROGRESS,
            OrderStatus.WORK_DONE,
        ]
    )
    reason = serializers.CharField(required=False, allow_blank=True)


class OrderCompleteSerializer(serializers.Serializer):
    final_price_uzs = serializers.IntegerField(min_value=1)
    payment_method = serializers.ChoiceField(choices=["cash"], default="cash")


class OrderCancelSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(choices=OrderCancelReason.choices, required=False)
    comment = serializers.CharField(required=False, allow_blank=True)

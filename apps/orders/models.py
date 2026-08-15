import uuid

from django.conf import settings
from django.db import models


class OrderStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SEARCHING = "searching", "Searching"
    OFFERED_TO_MASTER = "offered_to_master", "Offered to master"
    ACCEPTED_BY_MASTER = "accepted_by_master", "Accepted by master"
    PRICE_PROPOSED = "price_proposed", "Price proposed"
    PRICE_ACCEPTED = "price_accepted", "Price accepted"
    MASTER_ON_WAY = "master_on_way", "Master on way"
    MASTER_ARRIVED = "master_arrived", "Master arrived"
    IN_PROGRESS = "in_progress", "In progress"
    WORK_DONE = "work_done", "Work done"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"
    DISPUTED = "disputed", "Disputed"


class OrderCancelReason(models.TextChoices):
    CLIENT_CANCELLED = "client_cancelled", "Client cancelled"
    MASTER_DECLINED = "master_declined", "Master declined"
    PRICE_REJECTED = "price_rejected", "Price rejected"
    MASTER_NO_RESPONSE = "master_no_response", "Master no response"
    NO_MASTER_FOUND = "no_master_found", "No master found"
    REPLACED_BY_NEW_ORDER = "replaced_by_new_order", "Replaced by a newer order"
    SYSTEM = "system", "System"


class Order(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="client_orders")
    master = models.ForeignKey(
        "masters.MasterProfile",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="orders",
    )
    category = models.ForeignKey("masters.ServiceCategory", on_delete=models.PROTECT, related_name="orders")
    # Client picked a specific master from the directory: they get the first
    # (direct) offer; if they decline/expire, matching falls back to normal.
    preferred_master = models.ForeignKey(
        "masters.MasterProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="preferred_orders",
    )
    status = models.CharField(max_length=32, choices=OrderStatus.choices, default=OrderStatus.DRAFT)

    description = models.TextField(blank=True)
    address_text = models.CharField(max_length=255)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    budget_ceiling_uzs = models.PositiveIntegerField(null=True, blank=True)
    # Client doesn't mind the price and is happy for the master to estimate on site.
    price_flexible = models.BooleanField(default=False)

    agreed_price_uzs = models.PositiveIntegerField(null=True, blank=True)
    final_price_uzs = models.PositiveIntegerField(null=True, blank=True)
    payment_method = models.CharField(max_length=32, blank=True)

    cancellation_reason = models.CharField(max_length=64, choices=OrderCancelReason.choices, blank=True)
    cancellation_comment = models.TextField(blank=True)

    # How many times the client asked for a different master on this order. After
    # the configured threshold the order is routed to an operator.
    client_refusals = models.PositiveSmallIntegerField(default=0)
    needs_operator = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.category} / {self.client} / {self.status}"


class OrderAttachment(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="attachments")
    file = models.ImageField(upload_to="orders/%Y/%m/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self) -> str:
        return f"{self.order_id} / {self.file.name}"


class MasterOfferStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    EXPIRED = "expired", "Expired"


class MasterOffer(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="master_offers")
    master = models.ForeignKey("masters.MasterProfile", on_delete=models.CASCADE, related_name="order_offers")
    status = models.CharField(max_length=32, choices=MasterOfferStatus.choices, default=MasterOfferStatus.PENDING)
    score = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    radius_km = models.PositiveSmallIntegerField(default=1)
    expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.order_id} -> {self.master} / {self.status}"


class PriceProposalStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"
    WITHDRAWN = "withdrawn", "Withdrawn"


class PriceProposal(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="price_proposals")
    master = models.ForeignKey("masters.MasterProfile", on_delete=models.PROTECT, related_name="price_proposals")
    amount_uzs = models.PositiveIntegerField()
    status = models.CharField(max_length=32, choices=PriceProposalStatus.choices, default=PriceProposalStatus.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.order_id}: {self.amount_uzs} UZS / {self.status}"


class OrderEvent(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    from_status = models.CharField(max_length=32, blank=True)
    to_status = models.CharField(max_length=32)
    reason = models.CharField(max_length=120, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.order_id}: {self.from_status} -> {self.to_status}"

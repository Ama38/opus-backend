from django.conf import settings
from django.utils import timezone
from rest_framework import decorators, permissions, response, status as http_status, viewsets

from apps.orders.services import match_open_orders

from .models import (
    MasterCategoryPrice,
    MasterPortfolioItem,
    MasterProfile,
    MasterServiceStatus,
    MasterStatus,
    ServiceCategory,
)
from .serializers import (
    MasterAnalyticsSerializer,
    MasterCategoryPriceSerializer,
    MasterLocationSerializer,
    MasterPortfolioItemSerializer,
    MasterProfileSerializer,
    MasterPublicSerializer,
    ServiceCategorySerializer,
)
from .services import (
    get_master_analytics,
    get_or_create_master_profile,
    master_has_active_subscription,
)


class ServiceCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ServiceCategory.objects.filter(is_active=True).order_by("sort_order")
    serializer_class = ServiceCategorySerializer
    permission_classes = [permissions.AllowAny]


MAX_MASTER_SERVICES = 3


class MasterPortfolioViewSet(viewsets.ModelViewSet):
    """The signed-in master's own portfolio items (add photo / list / delete)."""

    serializer_class = MasterPortfolioItemSerializer

    def get_queryset(self):
        return MasterPortfolioItem.objects.filter(master__user=self.request.user).select_related("category")

    def perform_create(self, serializer):
        profile = get_or_create_master_profile(self.request.user)
        serializer.save(master=profile)


class MasterServiceViewSet(viewsets.ModelViewSet):
    """The master's own directions/services (up to 3, each moderated)."""

    serializer_class = MasterCategoryPriceSerializer

    def get_queryset(self):
        return MasterCategoryPrice.objects.filter(
            master__user=self.request.user
        ).select_related("category")

    def perform_create(self, serializer):
        profile = get_or_create_master_profile(self.request.user)
        if profile.category_prices.count() >= MAX_MASTER_SERVICES:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"code": "max_services_reached", "limit": MAX_MASTER_SERVICES})
        # New services go to moderation, unless auto-approve (test) mode is on.
        new_status = (
            MasterServiceStatus.APPROVED
            if getattr(settings, "MASTERGO_AUTO_APPROVE_MASTERS", False)
            else MasterServiceStatus.PENDING
        )
        serializer.save(master=profile, status=new_status)

    @decorators.action(detail=True, methods=["post"], url_path="toggle")
    def toggle(self, request, pk=None):
        service = self.get_object()
        # Cannot disable the last enabled service — at least one must stay on.
        if service.is_active:
            active_count = self.get_queryset().filter(is_active=True).count()
            if active_count <= 1:
                return response.Response(
                    {"code": "at_least_one_service_required"},
                    status=http_status.HTTP_400_BAD_REQUEST,
                )
        service.is_active = not service.is_active
        service.save(update_fields=["is_active"])
        return response.Response(self.get_serializer(service).data)


class MasterProfileViewSet(viewsets.ModelViewSet):
    serializer_class = MasterProfileSerializer

    def get_queryset(self):
        user = self.request.user
        queryset = MasterProfile.objects.select_related("user").prefetch_related("category_prices__category", "wallet")
        if user.is_staff:
            return queryset
        return queryset.filter(user=user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @decorators.action(detail=False, methods=["get", "put", "patch"], url_path="me")
    def me(self, request):
        profile = MasterProfile.objects.filter(user=request.user).first()
        if request.method == "GET":
            if profile is None:
                return response.Response({"profile": None})
            return response.Response({"profile": self.get_serializer(profile).data})

        serializer = self.get_serializer(profile, data=request.data, partial=request.method == "PATCH")
        serializer.is_valid(raise_exception=True)
        profile = serializer.save()
        return response.Response({"profile": self.get_serializer(profile).data})

    @decorators.action(detail=False, methods=["get"], url_path="me/analytics")
    def me_analytics(self, request):
        profile = MasterProfile.objects.filter(user=request.user).first()
        if profile is None:
            return response.Response({"code": "master_profile_required"}, status=403)
        analytics = get_master_analytics(profile)
        return response.Response(MasterAnalyticsSerializer(analytics).data)

    @decorators.action(detail=False, methods=["post"], url_path="go-online")
    def go_online(self, request):
        profile = get_or_create_master_profile(request.user)
        if profile.status != MasterStatus.APPROVED:
            return response.Response({"code": "master_not_approved"}, status=400)
        if not master_has_active_subscription(profile):
            return response.Response({"code": "no_active_package"}, status=400)
        profile.is_online = True
        profile.last_seen_at = timezone.now()
        profile.save(update_fields=["is_online", "last_seen_at", "updated_at"])
        match_open_orders()
        return response.Response({"profile": self.get_serializer(profile).data})

    @decorators.action(detail=False, methods=["post"], url_path="go-offline")
    def go_offline(self, request):
        profile = get_or_create_master_profile(request.user)
        profile.is_online = False
        profile.last_seen_at = timezone.now()
        profile.save(update_fields=["is_online", "last_seen_at", "updated_at"])
        return response.Response({"profile": self.get_serializer(profile).data})

    @decorators.action(detail=False, methods=["post"], url_path="location")
    def update_location(self, request):
        profile = get_or_create_master_profile(request.user)
        serializer = MasterLocationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile.current_latitude = serializer.validated_data["latitude"]
        profile.current_longitude = serializer.validated_data["longitude"]
        profile.last_seen_at = timezone.now()
        profile.save(update_fields=["current_latitude", "current_longitude", "last_seen_at", "updated_at"])
        if profile.is_online:
            match_open_orders()
        return response.Response({"profile": self.get_serializer(profile).data})

    def _public_masters(self):
        """Approved masters visible to clients, with their categories preloaded."""
        return (
            MasterProfile.objects.filter(status=MasterStatus.APPROVED)
            .select_related("user")
            .prefetch_related("category_prices__category")
        )

    @decorators.action(detail=False, methods=["get"], url_path="directory")
    def directory(self, request):
        """Client-facing master list. `?online=1` -> only masters on the map now;
        `?category=<slug>` -> filter by an approved service category."""
        queryset = self._public_masters()
        category = request.query_params.get("category")
        if category:
            queryset = queryset.filter(
                category_prices__category__slug=category,
                category_prices__is_active=True,
                category_prices__status=MasterServiceStatus.APPROVED,
            ).distinct()
        if request.query_params.get("online") in {"1", "true", "True"}:
            queryset = queryset.filter(is_online=True)
        queryset = queryset.order_by("-completed_orders_count", "-rating")
        data = MasterPublicSerializer(queryset, many=True, context={"request": request}).data
        return response.Response({"masters": data, "count": len(data)})

    @decorators.action(detail=False, methods=["get"], url_path="leaderboard")
    def leaderboard(self, request):
        """Top masters (клиентские «Лидеры»). Uses the weekly snapshot rank when
        available (TZ §7.2), otherwise live order by completed orders/rating."""
        from django.db.models import F

        limit = min(int(request.query_params.get("limit", 20) or 20), 100)
        queryset = self._public_masters()
        if queryset.filter(leaderboard_rank__isnull=False).exists():
            queryset = queryset.filter(leaderboard_rank__isnull=False).order_by(
                F("leaderboard_rank").asc()
            )[:limit]
        else:
            queryset = queryset.order_by("-completed_orders_count", "-rating")[:limit]
        data = MasterPublicSerializer(queryset, many=True, context={"request": request}).data
        return response.Response({"masters": data, "count": len(data)})

    @decorators.action(detail=True, methods=["get"], url_path="public")
    def public_profile(self, request, pk=None):
        """Single master card for the client-side profile screen."""
        profile = self._public_masters().filter(pk=pk).first()
        if profile is None:
            return response.Response({"code": "master_not_found"}, status=404)
        return response.Response(
            {"master": MasterPublicSerializer(profile, context={"request": request}).data}
        )

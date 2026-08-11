from rest_framework import decorators, permissions, response, status, viewsets

from apps.masters.services import get_or_create_master_profile

from .models import MasterWallet, Package
from .serializers import (
    MasterSubscriptionSerializer,
    MasterWalletSerializer,
    PackagePurchaseSerializer,
    PackageSerializer,
    WalletTopUpSerializer,
)
from .services import get_or_create_subscription, request_package, top_up_wallet


class PackageViewSet(viewsets.ReadOnlyModelViewSet):
    """Catalog of subscription packages (admin-configurable)."""

    serializer_class = PackageSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Package.objects.filter(is_active=True)


class SubscriptionViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    @decorators.action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        profile = get_or_create_master_profile(request.user)
        subscription = get_or_create_subscription(profile)
        purchases = profile.package_purchases.all()[:20]
        return response.Response(
            {
                "subscription": MasterSubscriptionSerializer(subscription).data,
                "history": PackagePurchaseSerializer(purchases, many=True).data,
            }
        )

    @decorators.action(detail=False, methods=["post"], url_path="request-package")
    def request_package_action(self, request):
        profile = get_or_create_master_profile(request.user)
        package = Package.objects.filter(id=request.data.get("package_id"), is_active=True).first()
        if package is None:
            return response.Response({"code": "package_not_found"}, status=status.HTTP_404_NOT_FOUND)
        purchase = request_package(profile, package, actor=request.user)
        subscription = get_or_create_subscription(profile)
        return response.Response(
            {
                "purchase": PackagePurchaseSerializer(purchase).data,
                "subscription": MasterSubscriptionSerializer(subscription).data,
            },
            status=status.HTTP_201_CREATED,
        )


class MasterWalletViewSet(viewsets.ReadOnlyModelViewSet):
    """Deprecated money wallet (kept for backwards compatibility)."""

    serializer_class = MasterWalletSerializer

    def get_queryset(self):
        queryset = MasterWallet.objects.select_related("master__user").prefetch_related("ledger_entries")
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(master__user=self.request.user)

    @decorators.action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        profile = get_or_create_master_profile(request.user)
        wallet, _ = MasterWallet.objects.get_or_create(master=profile)
        return response.Response({"wallet": self.get_serializer(wallet).data})

    @decorators.action(detail=False, methods=["post"], url_path="top-up")
    def top_up_me(self, request):
        profile = get_or_create_master_profile(request.user)
        wallet, _ = MasterWallet.objects.get_or_create(master=profile)
        serializer = WalletTopUpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wallet = top_up_wallet(
            wallet,
            serializer.validated_data["amount_uzs"],
            serializer.validated_data.get("note", "Prototype manual top-up"),
            created_by=request.user,
        )
        return response.Response({"wallet": self.get_serializer(wallet).data})

from rest_framework.routers import DefaultRouter

from .views import MasterWalletViewSet, PackageViewSet, SubscriptionViewSet


router = DefaultRouter()
router.register("wallets", MasterWalletViewSet, basename="wallet")
router.register("packages", PackageViewSet, basename="package")
router.register("subscription", SubscriptionViewSet, basename="subscription")

urlpatterns = router.urls


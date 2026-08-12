from rest_framework.routers import DefaultRouter

from .views import (
    MasterPortfolioPostViewSet,
    MasterPortfolioViewSet,
    MasterProfileViewSet,
    MasterServiceViewSet,
    ServiceCategoryViewSet,
)


router = DefaultRouter()
router.register("categories", ServiceCategoryViewSet, basename="category")
router.register("master-services", MasterServiceViewSet, basename="master-service")
router.register("master-portfolio", MasterPortfolioViewSet, basename="master-portfolio")
router.register("master-portfolio-posts", MasterPortfolioPostViewSet, basename="master-portfolio-post")
router.register("masters", MasterProfileViewSet, basename="master")

urlpatterns = router.urls


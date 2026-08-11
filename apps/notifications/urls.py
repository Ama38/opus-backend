from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    NotificationEventViewSet,
    register_device_token,
    unregister_device_token,
)


router = DefaultRouter()
router.register("notifications", NotificationEventViewSet, basename="notification")

urlpatterns = [
    path("notifications/device-token/", register_device_token, name="device-token-register"),
    path("notifications/device-token/remove/", unregister_device_token, name="device-token-remove"),
    *router.urls,
]

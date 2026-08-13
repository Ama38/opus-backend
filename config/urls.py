from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path, re_path
from django.views.static import serve as static_serve


def health_check(request):
    return JsonResponse({"status": "ok", "service": "mastergo-backend"})


def readiness_check(request):
    components = {"database": False, "cache": False}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            components["database"] = cursor.fetchone()[0] == 1
    except Exception:
        pass
    try:
        key = "mastergo:readiness"
        cache.set(key, "ok", timeout=5)
        components["cache"] = cache.get(key) == "ok"
    except Exception:
        pass
    ready = all(components.values())
    return JsonResponse(
        {"status": "ready" if ready else "degraded", "components": components},
        status=200 if ready else 503,
    )


def api_index(request):
    return JsonResponse(
        {
            "auth": "/api/auth/",
            "categories": "/api/categories/",
            "masters": "/api/masters/",
            "wallets": "/api/wallets/",
            "orders": "/api/orders/",
            "chat_rooms": "/api/chat/rooms/",
            "chat_messages": "/api/chat/messages/",
            "geo": "/api/geo/master-pings/",
            "reviews": "/api/reviews/",
            "support": "/api/support/cases/",
            "notifications": "/api/notifications/",
        }
    )


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api_index, name="api_index"),
    path("api/health/", health_check, name="health_check"),
    path("api/ready/", readiness_check, name="readiness_check"),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/", include("apps.masters.urls")),
    path("api/", include("apps.billing.urls")),
    path("api/", include("apps.orders.urls")),
    path("api/", include("apps.chat.urls")),
    path("api/", include("apps.geo.urls")),
    path("api/", include("apps.reviews.urls")),
    path("api/", include("apps.support.urls")),
    path("api/", include("apps.notifications.urls")),
    # User-uploaded media (avatars, chat photos/videos). Fine for the prototype;
    # move to object storage (S3) before real scale.
    re_path(r"^media/(?P<path>.*)$", static_serve, {"document_root": settings.MEDIA_ROOT}),
]

from pathlib import Path
from urllib.parse import urlparse, unquote
import os


BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BASE_DIR.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def database_from_url(database_url: str) -> dict[str, str | int]:
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("DATABASE_URL must use postgres:// or postgresql://")

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": parsed.port or 5432,
    }


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-mastergo-secret")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]
if railway_public_domain := os.getenv("RAILWAY_PUBLIC_DOMAIN"):
    ALLOWED_HOSTS.append(railway_public_domain)

INSTALLED_APPS = [
    "jazzmin",  # modern admin theme (must be before django.contrib.admin)
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "channels",
    "corsheaders",
    "apps.accounts",
    "apps.masters",
    "apps.billing",
    "apps.orders",
    "apps.chat",
    "apps.geo",
    "apps.reviews",
    "apps.support",
    "apps.notifications",
    "apps.platform_config",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

database_url = os.getenv("DATABASE_URL")

if database_url:
    DATABASES = {"default": database_from_url(database_url)}
elif env_bool("MASTERGO_USE_SQLITE", False):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv("MASTERGO_SQLITE_PATH", BASE_DIR / "db.sqlite3"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "mastergo"),
            "USER": os.getenv("POSTGRES_USER", "mastergo"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", "mastergo"),
            "HOST": os.getenv("POSTGRES_HOST", "localhost"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
        }
    }

if env_bool("MASTERGO_USE_INMEMORY_CHANNELS", False):
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        }
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [os.getenv("REDIS_URL", "redis://localhost:6379/0")],
            },
        }
    }

# OTP codes and throttle counters live in the cache, so it must be shared
# across worker processes in production. Use Redis when available; fall back
# to per-process memory only for local single-process dev.
_REDIS_URL = os.getenv("REDIS_URL")
if _REDIS_URL and not env_bool("MASTERGO_USE_INMEMORY_CHANNELS", False):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "mastergo-dev-cache",
        }
    }

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru"
LANGUAGES = [
    ("ru", "Russian"),
    ("uz", "Uzbek"),
]
TIME_ZONE = "Asia/Tashkent"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
# Leading slash is required: without it FileField.url is a *relative* path and
# request.build_absolute_uri() resolves it against the request path (e.g.
# /api/media/... instead of /media/...), so avatars/attachments 404 in the apps.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("DJANGO_CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
if railway_public_domain:
    CSRF_TRUSTED_ORIGINS.append(f"https://{railway_public_domain}")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", False)
SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("DJANGO_CSRF_COOKIE_SECURE", not DEBUG)

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

MASTERGO_MOCK_OTP = env_bool("MASTERGO_MOCK_OTP", False)
MASTERGO_MOCK_OTP_CODE = os.getenv("MASTERGO_MOCK_OTP_CODE", "1111")
MASTERGO_MIN_MASTER_BALANCE_UZS = 40_000  # deprecated: money-balance gate replaced by packages

# --- Subscription / packages (v3) ---
# Days a package is valid from activation. TZ default 30; recommends 90 for launch.
MASTERGO_PACKAGE_EXPIRY_DAYS = int(os.getenv("MASTERGO_PACKAGE_EXPIRY_DAYS", "90"))
# Launch free period: packages are activated immediately at no cost.
MASTERGO_FREE_PACKAGES = env_bool("MASTERGO_FREE_PACKAGES", True)
# TEST MODE: every new master is auto-approved and granted a test package so
# they can go online without operator moderation. Set to 0 for production.
MASTERGO_AUTO_APPROVE_MASTERS = env_bool("MASTERGO_AUTO_APPROVE_MASTERS", True)

# --- Matching (v3) ---
MASTERGO_MATCHING_RADII_KM = (3, 7, 0)  # 0 = whole city (no distance cap)
MASTERGO_OFFER_TTL_SECONDS = int(os.getenv("MASTERGO_OFFER_TTL_SECONDS", "60"))
MASTERGO_RADIUS_EXPAND_SECONDS = int(os.getenv("MASTERGO_RADIUS_EXPAND_SECONDS", "180"))
MASTERGO_MATCH_WEIGHT_DISTANCE = 0.50
MASTERGO_MATCH_WEIGHT_RATING = 0.30
MASTERGO_MATCH_WEIGHT_COMPLETION = 0.10
MASTERGO_MATCH_WEIGHT_REACTION = 0.10
MASTERGO_STARTER_RATING = float(os.getenv("MASTERGO_STARTER_RATING", "4.5"))
MASTERGO_NEWCOMER_ORDER_THRESHOLD = 10  # < this many completed = "newcomer"
MASTERGO_NEWCOMER_PRIORITY_EVERY = 5    # every Nth order prioritises a newcomer
MASTERGO_CLIENT_REFUSALS_TO_OPERATOR = 3

MASTERGO_OFFER_EXPIRATION_TIMER_ENABLED = env_bool("MASTERGO_OFFER_EXPIRATION_TIMER_ENABLED", True)
OSRM_ENABLED = env_bool("OSRM_ENABLED", False)
OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org")

# 2GIS API key (maps tiles + geocoder/suggest). Demo key for the prototype;
# override via env in real deployments.
MASTERGO_2GIS_KEY = os.getenv("MASTERGO_2GIS_KEY", "502c1521-9ee2-4de8-bec6-15b5f882c08e")

# --- SMS / OTP delivery ---------------------------------------------------
# When MASTERGO_MOCK_OTP is on, the code is fixed and no SMS is sent.
# Otherwise: dry-run logs the code to the console (dev), production sends via
# the configured provider. Defaults to dry-run while DEBUG so local runs never
# hit a real gateway by accident.
SMS_DRY_RUN = env_bool("SMS_DRY_RUN", DEBUG)
SMS_PROVIDER = os.getenv("SMS_PROVIDER", "eskiz")
ESKIZ_EMAIL = os.getenv("ESKIZ_EMAIL", "")
ESKIZ_PASSWORD = os.getenv("ESKIZ_PASSWORD", "")
ESKIZ_BASE_URL = os.getenv("ESKIZ_BASE_URL", "https://notify.eskiz.uz/api")
# Eskiz test sender until a branded alphaname is approved.
ESKIZ_SENDER = os.getenv("ESKIZ_SENDER", "4546")
OTP_SMS_TEMPLATE = os.getenv(
    "OTP_SMS_TEMPLATE",
    "MasterGo: tasdiqlash kodi {code}. Hech kimga bermang.",
)


# --- Admin theme (django-jazzmin) ---
JAZZMIN_SETTINGS = {
    "site_title": "Opus Admin",
    "site_header": "Opus",
    "site_brand": "Opus",
    "welcome_sign": "Opus — панель оператора",
    "copyright": "Opus",
    "search_model": ["accounts.User", "masters.MasterProfile", "orders.Order"],
    "topmenu_links": [
        {"name": "Заказы", "model": "orders.order"},
        {"name": "Мастера", "model": "masters.masterprofile"},
        {"name": "Заявки на пакеты", "model": "billing.packagepurchase"},
    ],
    "icons": {
        "accounts.User": "fas fa-user",
        "masters.MasterProfile": "fas fa-user-gear",
        "masters.ServiceCategory": "fas fa-list",
        "orders.Order": "fas fa-clipboard-list",
        "billing.Package": "fas fa-box",
        "billing.MasterSubscription": "fas fa-id-card",
        "billing.PackagePurchase": "fas fa-receipt",
        "chat.ChatRoom": "fas fa-comments",
        "reviews.Review": "fas fa-star",
        "support.SupportCase": "fas fa-headset",
        "notifications.NotificationEvent": "fas fa-bell",
        "notifications.DeviceToken": "fas fa-mobile-screen",
    },
    "order_with_respect_to": ["orders", "masters", "billing", "chat", "reviews", "support", "accounts"],
    "changeform_format": "horizontal_tabs",
    "related_modal_active": True,
}

JAZZMIN_UI_TWEAKS = {
    "theme": "flatly",
    "dark_mode_theme": "darkly",
    "navbar": "navbar-dark",
    "navbar_fixed": True,
    "sidebar_fixed": True,
    "sidebar": "sidebar-dark-primary",
    "brand_colour": "navbar-primary",
    "accent": "accent-primary",
    "button_classes": {
        "primary": "btn-primary",
        "success": "btn-success",
        "danger": "btn-danger",
    },
}

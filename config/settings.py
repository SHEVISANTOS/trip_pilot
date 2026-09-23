"""
Django settings for the TripPilot AI project.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, True),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="django-insecure-dev-only-change-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1" , "192.168.1.207"])


# Application definition

INSTALLED_APPS = [
    "unfold",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_ratelimit",
    "accounts",
    "trips",
    "integrations",
    "pdfexport",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
# DATABASE_URL, e.g. postgres://user:pass@host:5432/dbname — defaults to local sqlite.

DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
}


# Cache — Redis when REDIS_URL is set, otherwise an in-process cache so the
# project runs with zero extra services for local development.

REDIS_URL = env("REDIS_URL", default="")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                # A Redis outage/DNS blip must degrade to "cache miss", never
                # crash the request. integrations/base.py's cache.get() calls
                # sit outside any try/except (a miss is meant to be cheap and
                # unconditional), and django-ratelimit's counter reads go
                # through this same cache — without this, either one takes
                # the whole site down the moment Redis is unreachable, which
                # is exactly what happened here (ConnectionError on POST /).
                "IGNORE_EXCEPTIONS": True,
            },
        }
    }
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Cache TTLs (seconds) for integration responses, per README section 5.
CACHE_TTL_FLIGHTS_HOTELS = env.int("CACHE_TTL_FLIGHTS_HOTELS", default=6 * 60 * 60)
# CACHE_TTL_VISA is unused: visa guidance is now a local CSV lookup (see
# integrations/visa.py), not a network call, so there's nothing to cache —
# kept in case a paid Timatic-style API replaces it later.
CACHE_TTL_VISA = env.int("CACHE_TTL_VISA", default=7 * 24 * 60 * 60)
CACHE_TTL_EXCHANGE_RATE = env.int("CACHE_TTL_EXCHANGE_RATE", default=60 * 60)
CACHE_TTL_ATTRACTIONS = env.int("CACHE_TTL_ATTRACTIONS", default=24 * 60 * 60)
CACHE_TTL_ESIM = env.int("CACHE_TTL_ESIM", default=6 * 60 * 60)
# Hotel *names* from OSM change rarely, so this can be cached for a week.
CACHE_TTL_HOTELS = env.int("CACHE_TTL_HOTELS", default=7 * 24 * 60 * 60)
# Distances don't change, so this can be cached far longer than pricing data.
CACHE_TTL_MAPS = env.int("CACHE_TTL_MAPS", default=30 * 24 * 60 * 60)
# Much longer than CACHE_TTL_FLIGHTS_HOTELS — SerpApi's 100/month cap makes
# quota, not freshness, the binding constraint here.
CACHE_TTL_SERPAPI = env.int("CACHE_TTL_SERPAPI", default=24 * 60 * 60)

# Celery — trips.tasks.build_and_persist_plan_task runs the multi-provider
# plan generation off the request thread. `or REDIS_URL` (not env()'s
# `default=`) so CELERY_BROKER_URL/CELERY_RESULT_BACKEND=<empty> in .env
# correctly falls through to REDIS_URL — django-environ doesn't do shell-style
# ${REDIS_URL} expansion, so that syntax in .env would silently pass through
# as a literal string.
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="") or REDIS_URL or "memory://"
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="") or REDIS_URL or "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=True)


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# Static & media files

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:dashboard"
LOGOUT_REDIRECT_URL = "trips:planner"


# External API credentials — all optional; every integrations/*.py client
# falls back to illustrative demo data when its key(s) are missing.

TRAVELPAYOUTS_TOKEN = env("TRAVELPAYOUTS_TOKEN", default="")
OPENTRIPMAP_API_KEY = env("OPENTRIPMAP_API_KEY", default="")
GOOGLE_MAPS_API_KEY = env("GOOGLE_MAPS_API_KEY", default="")
SAFETYWING_API_KEY = env("SAFETYWING_API_KEY", default="")
ESIM_GO_API_KEY = env("ESIM_GO_API_KEY", default="")
LITEAPI_KEY = env("LITEAPI_KEY", default="")
STAYAPI_KEY = env("STAYAPI_KEY", default="")
# SerpApi (real Google Flights / Google Hotels / Google Search results) — a
# second live tier for flights and hotels, and a link upgrade for
# attractions. Free tier is a hard 100 searches/month shared across all
# three, so every SerpApi call is cached far longer than the other providers
# (CACHE_TTL_SERPAPI below) and only reached when the primary source above it
# in the cascade has nothing.
SERPAPI_KEY = env("SERPAPI_KEY", default="")

# Visa guidance: no key needed — backed by the open, MIT-licensed Passport
# Index dataset vendored at integrations/data/passport_index_visa.csv
# (see integrations/visa.py). Override to point at a refreshed copy.
# `or` (not env()'s `default=`) handles VISA_DATASET_PATH=<empty> in .env —
# django-environ's default only kicks in when the var is absent entirely.
VISA_DATASET_PATH = env("VISA_DATASET_PATH", default="") or str(
    BASE_DIR / "integrations" / "data" / "passport_index_visa.csv"
)


# Rate limiting (django-ratelimit) for the planner endpoint.
RATELIMIT_ENABLE = env.bool("RATELIMIT_ENABLE", default=True)
# django-ratelimit's own default is to fail *closed*: if it can't read its
# counter (e.g. the Redis outage that motivated this setting), it blocks the
# request rather than risk letting an unlimited burst through. That's the
# right default for a limiter guarding something dangerous, but every
# integration client here already has its own independent fallback — a
# Redis blip doesn't touch Travelpayouts/LiteAPI/etc, it only means caching
# and rate-limiting are briefly unavailable. Failing open keeps the planner
# usable during that window instead of a blanket 403 on every submission.
RATELIMIT_FAIL_OPEN = env.bool("RATELIMIT_FAIL_OPEN", default=True)

if not REDIS_URL:
    # LocMemCache isn't a shared cache, so it's not officially supported by
    # django-ratelimit — fine for single-process local dev; switches to the
    # supported Redis backend automatically once REDIS_URL is set.
    SILENCED_SYSTEM_CHECKS = ["django_ratelimit.E003"]


# django-unfold admin theming — palette matches static/style.css:
# --ink:#14213d --blue:#276ef1 --soft:#f4f7fc --green:#138a57 --orange:#e8861a
UNFOLD = {
    "SITE_TITLE": "TripPilot AI Admin",
    "SITE_HEADER": "TripPilot AI",
    "SITE_SUBHEADER": "Operations dashboard",
    "SITE_SYMBOL": "flight_takeoff",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "COLORS": {
        "primary": {
            "50": "#eff6ff",
            "100": "#dbeafe",
            "200": "#bfdbfe",
            "300": "#93c5fd",
            "400": "#5b93f5",
            "500": "#3a7ef3",
            "600": "#276ef1",
            "700": "#1d4ed8",
            "800": "#1e40af",
            "900": "#1e3a8a",
            "950": "#14213d",
        },
    },
    "DASHBOARD_CALLBACK": "trips.admin_dashboard.dashboard_callback",
    "SITE_VIEWS": [
        ("integrations/health/", "integration-health", "integrations.views.IntegrationHealthView"),
    ],
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "Trips",
                "separator": True,
                "items": [
                    {"title": "Trip requests", "icon": "map", "link": "/admin/trips/triprequest/"},
                    {"title": "Saved trips", "icon": "bookmark", "link": "/admin/trips/savedtrip/"},
                    {"title": "Itineraries", "icon": "calendar_month", "link": "/admin/trips/itinerary/"},
                ],
            },
            {
                "title": "Operations",
                "separator": True,
                "items": [
                    {
                        "title": "Integration health",
                        "icon": "monitor_heart",
                        "link": "/admin/integrations/health/",
                    },
                ],
            },
        ],
    },
}

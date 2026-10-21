"""Django settings for Scoutly.

Everything that differs between your machine and production comes from environment variables.
For local development, copy .env.example to .env: it's read at startup and never committed.
"""
import os
from pathlib import Path

import django_mongodb_backend
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path):
    """Minimal .env reader (KEY=value lines). Real environment variables win over the file."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv(BASE_DIR / ".env")


def env(key, default=""):
    return os.environ.get(key, default).strip()


def env_bool(key, default=False):
    return env(key, "1" if default else "0").lower() in ("1", "true", "yes", "on")


def env_list(key, default=""):
    return [v.strip() for v in env(key, default).split(",") if v.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured(
            "Set DJANGO_SECRET_KEY. For local development, copy .env.example to .env (it turns on DEBUG and sets a dev key)."
        )
    SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "config.apps.MongoAdminConfig",
    "config.apps.MongoAuthConfig",
    "config.apps.MongoContentTypesConfig",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "accounts",
    "seo",
    "workspace",
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
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

# MongoDB through the official backend (django-mongodb-backend). MONGODB_URI is a standard
# connection string: mongodb://localhost:27017 locally, mongodb+srv://… for MongoDB Atlas.
DATABASES = {
    "default": django_mongodb_backend.parse_uri(env("MONGODB_URI", "mongodb://localhost:27017"), db_name=env("MONGODB_NAME", "scoutly")),
}
# Primary keys are MongoDB ObjectIds. Django's built-in apps get the same through config/apps.py,
# and their migrations are generated for ObjectIds into mongo_migrations/.
DEFAULT_AUTO_FIELD = "django_mongodb_backend.fields.ObjectIdAutoField"
MIGRATION_MODULES = {
    "admin": "mongo_migrations.admin",
    "auth": "mongo_migrations.auth",
    "contenttypes": "mongo_migrations.contenttypes",
}

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Rate limits and login throttling use the cache. The default in-process cache is fine for one
# server process; with several workers, point this at Redis or Memcached so they share counts.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "scoutly"}}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# /static/ is only Django admin's CSS and JS (run collectstatic in production).
# The site's own pages and assets are served from the repository root by core.views.site.
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
SITE_ROOT = BASE_DIR

# Search Console exports saved by Position Tracking can be a few MB.
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

# The browser reads the CSRF cookie to send X-CSRFToken with account and workspace requests.
CSRF_COOKIE_HTTPONLY = False
CSRF_FAILURE_VIEW = "core.views.csrf_failure"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "0") or 0)
    if env_bool("DJANGO_BEHIND_PROXY", False):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

ADMIN_PATH = env("DJANGO_ADMIN_PATH", "admin/").strip("/") + "/"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "scoutly": {"handlers": ["console"], "level": "INFO"},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

# ------------------------------------------------------------------ Scoutly
# Requests per hour per visitor (by IP; by account once logged in, at the higher limit).
SCOUTLY_RATE_LIMITS = {
    "audit": (int(env("SCOUTLY_AUDITS_PER_HOUR", "20")), int(env("SCOUTLY_AUDITS_PER_HOUR_USER", "60"))),
    "tool": (int(env("SCOUTLY_TOOLS_PER_HOUR", "300")), int(env("SCOUTLY_TOOLS_PER_HOUR_USER", "1000"))),
    "signup": (10, 10),
}
# Paid DataForSEO lookups (run=1) spend the account owner's credit. Outside DEBUG they need a login.
SCOUTLY_PAID_REQUIRES_LOGIN = env_bool("SCOUTLY_PAID_REQUIRES_LOGIN", not DEBUG)
# Only trust X-Forwarded-For when a proxy you control sets it; otherwise visitors could spoof their IP.
SCOUTLY_TRUST_X_FORWARDED_FOR = env_bool("DJANGO_BEHIND_PROXY", False)

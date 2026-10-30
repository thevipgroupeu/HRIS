"""Minimal settings for the isolated public career.thevipgroup.eu process."""

from __future__ import annotations

import os
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _bool(name, default=False):
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


def _list(name, default):
    return [
        item.strip() for item in os.getenv(name, default).split(",") if item.strip()
    ]


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured(f"{name} must be an integer") from exc


DEBUG = _bool("DEBUG", False)
HORILLA_ENV = os.getenv("HORILLA_ENV", "")
if DEBUG and HORILLA_ENV.strip().lower() == "production":
    raise ImproperlyConfigured("DEBUG must be False when HORILLA_ENV=production")
SECRET_KEY = os.getenv(
    "CAREERHUB_SECRET_KEY",
    "careerhub-local-development-key" if DEBUG else "",
)
ALLOWED_HOSTS = _list(
    "CAREERHUB_ALLOWED_HOSTS",
    "career.thevipgroup.eu,localhost,127.0.0.1" if DEBUG else "career.thevipgroup.eu",
)
CSRF_TRUSTED_ORIGINS = _list(
    "CAREERHUB_CSRF_TRUSTED_ORIGINS",
    "https://career.thevipgroup.eu",
)

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "tvg_careers.accounts.apps.TvgCareerHubAccountsConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "tvg_careers.public_middleware.PublicSecurityHeadersMiddleware",
]
ROOT_URLCONF = "horilla.careerhub_urls"
WSGI_APPLICATION = "horilla.careerhub_wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "tvg_careers" / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {
            "loaders": ["django.template.loaders.filesystem.Loader"],
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

_database_url = os.getenv("CAREERHUB_DATABASE_URL", "")
if _database_url:
    DATABASES = {"default": environ.Env.db_url_config(_database_url)}
elif os.getenv("CAREERHUB_DB_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("CAREERHUB_DB_NAME", "careerhub_db"),
            "USER": os.getenv("CAREERHUB_DB_USER", "careerhub_user"),
            "PASSWORD": os.getenv("CAREERHUB_DB_PASSWORD", ""),
            "HOST": os.getenv("CAREERHUB_DB_HOST", "careerhub_db"),
            "PORT": os.getenv("CAREERHUB_DB_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv(
                "CAREERHUB_SQLITE_PATH",
                str(BASE_DIR / "careerhub-development.sqlite3"),
            ),
            "OPTIONS": {"timeout": 30},
        }
    }

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [
    BASE_DIR / "tvg_careers" / "static",
    BASE_DIR / "payroll" / "static",
]
MEDIA_ROOT = Path(
    os.getenv(
        "CAREERHUB_PRIVATE_MEDIA_ROOT",
        str(BASE_DIR / "careerhub-private-media"),
    )
)
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": MEDIA_ROOT},
    },
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedStaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

CAREERHUB_REDIS_URL = os.getenv("CAREERHUB_REDIS_URL", "")
CAREERHUB_REDIS_HOST = os.getenv("CAREERHUB_REDIS_HOST", "")
CAREERHUB_REDIS_PASSWORD = os.getenv("CAREERHUB_REDIS_PASSWORD", "")
if CAREERHUB_REDIS_URL:
    redis_location = CAREERHUB_REDIS_URL
    redis_options = {"CLIENT_CLASS": "django_redis.client.DefaultClient"}
elif CAREERHUB_REDIS_HOST:
    redis_location = "redis://{}:{}/{}".format(
        CAREERHUB_REDIS_HOST,
        _int("CAREERHUB_REDIS_PORT", 6379),
        _int("CAREERHUB_REDIS_DB", 0),
    )
    redis_options = {"CLIENT_CLASS": "django_redis.client.DefaultClient"}
    if CAREERHUB_REDIS_PASSWORD:
        redis_options["PASSWORD"] = CAREERHUB_REDIS_PASSWORD
else:
    redis_location = ""
    redis_options = {}

if redis_location:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": redis_location,
            "OPTIONS": redis_options,
            "KEY_PREFIX": "careerhub-auth",
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "careerhub-public-development",
        }
    }

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/Warsaw"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CAREERHUB_PUBLIC_BASE_URL = os.getenv(
    "CAREERHUB_PUBLIC_BASE_URL", "https://career.thevipgroup.eu"
)
CAREERHUB_PEOPLEOPS_API_URL = os.getenv(
    "CAREERHUB_PEOPLEOPS_API_URL",
    "http://web:8000/internal/careerhub/v1",
)
CAREERHUB_INTERNAL_API_TOKEN = os.getenv("CAREERHUB_INTERNAL_API_TOKEN", "")
CAREERHUB_IDENTITY_API_TOKEN = os.getenv("CAREERHUB_IDENTITY_API_TOKEN", "")
CAREERHUB_APPLICATION_API_TOKEN = os.getenv("CAREERHUB_APPLICATION_API_TOKEN", "")
CAREERHUB_VERIFICATION_API_TOKEN = os.getenv("CAREERHUB_VERIFICATION_API_TOKEN", "")
CAREERHUB_INTERVIEW_API_TOKEN = os.getenv("CAREERHUB_INTERVIEW_API_TOKEN", "")
CAREERHUB_ARRANGEMENT_API_TOKEN = os.getenv("CAREERHUB_ARRANGEMENT_API_TOKEN", "")
CAREERHUB_CONTACT_EMAIL = os.getenv(
    "CAREERHUB_CONTACT_EMAIL", "recruitment@thevipgroup.eu"
)
CAREERHUB_API_TIMEOUT = (2, 5)
CAREERHUB_PRIVACY_NOTICE_VERSION = os.getenv(
    "CAREERHUB_PRIVACY_NOTICE_VERSION", "2026-10-25"
)
CAREERHUB_EMAIL_VERIFICATION_TIMEOUT = _int(
    "CAREERHUB_EMAIL_VERIFICATION_TIMEOUT", 24 * 60 * 60
)
CAREERHUB_TRUSTED_PROXY_COUNT = _int("CAREERHUB_TRUSTED_PROXY_COUNT", 0)
CAREERHUB_AUTH_RATE_LIMITS = {
    "signup_ip": (_int("CAREERHUB_SIGNUP_IP_LIMIT", 10), 15 * 60),
    "signup_email": (_int("CAREERHUB_SIGNUP_EMAIL_LIMIT", 3), 60 * 60),
    "verification_ip": (_int("CAREERHUB_VERIFICATION_IP_LIMIT", 20), 60 * 60),
    "login_ip": (_int("CAREERHUB_LOGIN_IP_LIMIT", 30), 15 * 60),
    "login_pair": (_int("CAREERHUB_LOGIN_PAIR_LIMIT", 5), 15 * 60),
    "recovery_ip": (_int("CAREERHUB_RECOVERY_IP_LIMIT", 10), 60 * 60),
    "recovery_email": (_int("CAREERHUB_RECOVERY_EMAIL_LIMIT", 3), 60 * 60),
    "interview_account": (_int("CAREERHUB_INTERVIEW_ACCOUNT_LIMIT", 20), 60 * 60),
    "interview_ip": (_int("CAREERHUB_INTERVIEW_IP_LIMIT", 40), 60 * 60),
    "arrangement_account": (
        _int("CAREERHUB_ARRANGEMENT_ACCOUNT_LIMIT", 20),
        60 * 60,
    ),
    "arrangement_ip": (_int("CAREERHUB_ARRANGEMENT_IP_LIMIT", 40), 60 * 60),
}

AUTH_USER_MODEL = "tvg_careerhub_accounts.CandidateAccount"
AUTHENTICATION_BACKENDS = ["tvg_careers.accounts.backends.CandidateEmailBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "careerhub:candidate-login"
LOGIN_REDIRECT_URL = "careerhub:candidate-profile"
PASSWORD_RESET_TIMEOUT = _int("CAREERHUB_PASSWORD_RESET_TIMEOUT", 60 * 60)

EMAIL_BACKEND = os.getenv(
    "CAREERHUB_EMAIL_BACKEND",
    (
        "django.core.mail.backends.locmem.EmailBackend"
        if DEBUG
        else "django.core.mail.backends.smtp.EmailBackend"
    ),
)
EMAIL_HOST = os.getenv("CAREERHUB_EMAIL_HOST", "")
EMAIL_PORT = _int("CAREERHUB_EMAIL_PORT", 587)
EMAIL_HOST_USER = os.getenv("CAREERHUB_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("CAREERHUB_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = _bool("CAREERHUB_EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = _int("CAREERHUB_EMAIL_TIMEOUT", 10)
DEFAULT_FROM_EMAIL = os.getenv(
    "CAREERHUB_DEFAULT_FROM_EMAIL",
    "THEVIPGROUP Recruitment <recruitment@thevipgroup.eu>",
)

SESSION_COOKIE_NAME = "tvg_careerhub_sessionid"
CSRF_COOKIE_NAME = "tvg_careerhub_csrftoken"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = _int("CAREERHUB_SESSION_COOKIE_AGE", 4 * 60 * 60)
SESSION_SAVE_EVERY_REQUEST = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = _bool("CAREERHUB_SECURE_SSL_REDIRECT", not DEBUG)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_HSTS_SECONDS = 0 if DEBUG else 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
DATA_UPLOAD_MAX_MEMORY_SIZE = 1_048_576
DATA_UPLOAD_MAX_NUMBER_FIELDS = 50
FILE_UPLOAD_MAX_MEMORY_SIZE = 1_048_576

if not DEBUG:
    errors = []
    if (
        len(SECRET_KEY) < 50
        or len(set(SECRET_KEY)) < 5
        or SECRET_KEY.startswith(("careerhub-local", "django-insecure-", "change-me"))
    ):
        errors.append("CAREERHUB_SECRET_KEY must be a strong, separate secret")
    if not ALLOWED_HOSTS or any("*" in host for host in ALLOWED_HOSTS):
        errors.append("CAREERHUB_ALLOWED_HOSTS must contain explicit hostnames")
    if len(
        CAREERHUB_INTERNAL_API_TOKEN
    ) < 32 or CAREERHUB_INTERNAL_API_TOKEN.lower().startswith(("change-me", "dev-")):
        errors.append("CAREERHUB_INTERNAL_API_TOKEN must be configured")
    if len(
        CAREERHUB_IDENTITY_API_TOKEN
    ) < 32 or CAREERHUB_IDENTITY_API_TOKEN.lower().startswith(("change-me", "dev-")):
        errors.append("CAREERHUB_IDENTITY_API_TOKEN must be configured")
    if CAREERHUB_INTERNAL_API_TOKEN == CAREERHUB_IDENTITY_API_TOKEN:
        errors.append("CareerHub read and identity API tokens must be different")
    if len(
        CAREERHUB_APPLICATION_API_TOKEN
    ) < 32 or CAREERHUB_APPLICATION_API_TOKEN.lower().startswith(("change-me", "dev-")):
        errors.append("CAREERHUB_APPLICATION_API_TOKEN must be configured")
    if CAREERHUB_APPLICATION_API_TOKEN in {
        CAREERHUB_INTERNAL_API_TOKEN,
        CAREERHUB_IDENTITY_API_TOKEN,
    }:
        errors.append("CareerHub application API token must be distinct")
    if len(CAREERHUB_VERIFICATION_API_TOKEN) < 32 or (
        CAREERHUB_VERIFICATION_API_TOKEN.lower().startswith(("change-me", "dev-"))
    ):
        errors.append("CAREERHUB_VERIFICATION_API_TOKEN must be configured")
    if CAREERHUB_VERIFICATION_API_TOKEN in {
        CAREERHUB_INTERNAL_API_TOKEN,
        CAREERHUB_IDENTITY_API_TOKEN,
        CAREERHUB_APPLICATION_API_TOKEN,
    }:
        errors.append("CareerHub verification API token must be distinct")
    if len(CAREERHUB_INTERVIEW_API_TOKEN) < 32 or (
        CAREERHUB_INTERVIEW_API_TOKEN.lower().startswith(("change-me", "dev-"))
    ):
        errors.append("CAREERHUB_INTERVIEW_API_TOKEN must be configured")
    if CAREERHUB_INTERVIEW_API_TOKEN in {
        CAREERHUB_INTERNAL_API_TOKEN,
        CAREERHUB_IDENTITY_API_TOKEN,
        CAREERHUB_APPLICATION_API_TOKEN,
        CAREERHUB_VERIFICATION_API_TOKEN,
    }:
        errors.append("CareerHub interview API token must be distinct")
    if len(CAREERHUB_ARRANGEMENT_API_TOKEN) < 32 or (
        CAREERHUB_ARRANGEMENT_API_TOKEN.lower().startswith(("change-me", "dev-"))
    ):
        errors.append("CAREERHUB_ARRANGEMENT_API_TOKEN must be configured")
    if CAREERHUB_ARRANGEMENT_API_TOKEN in {
        CAREERHUB_INTERNAL_API_TOKEN,
        CAREERHUB_IDENTITY_API_TOKEN,
        CAREERHUB_APPLICATION_API_TOKEN,
        CAREERHUB_VERIFICATION_API_TOKEN,
        CAREERHUB_INTERVIEW_API_TOKEN,
    }:
        errors.append("CareerHub arrangement API token must be distinct")
    if not CAREERHUB_PUBLIC_BASE_URL.startswith("https://"):
        errors.append("CAREERHUB_PUBLIC_BASE_URL must use HTTPS")
    if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
        errors.append("CareerHub production accounts require PostgreSQL")
    if os.getenv("CAREERHUB_DB_HOST") and (
        not os.getenv("CAREERHUB_DB_PASSWORD")
        or os.getenv("CAREERHUB_DB_PASSWORD", "").lower().startswith("change-me")
    ):
        errors.append("CAREERHUB_DB_PASSWORD must be a production secret")
    if not redis_location:
        errors.append("CareerHub Redis is required for shared rate limits")
    if CAREERHUB_REDIS_HOST and (
        not CAREERHUB_REDIS_PASSWORD
        or CAREERHUB_REDIS_PASSWORD.lower().startswith("change-me")
    ):
        errors.append("CAREERHUB_REDIS_PASSWORD must be a production secret")
    if CAREERHUB_TRUSTED_PROXY_COUNT < 1:
        errors.append(
            "CAREERHUB_TRUSTED_PROXY_COUNT must match the production proxy chain"
        )
    if EMAIL_BACKEND.endswith(("locmem.EmailBackend", "console.EmailBackend")):
        errors.append(
            "CareerHub production email backend must not be local-memory/console"
        )
    if not EMAIL_HOST:
        errors.append("CAREERHUB_EMAIL_HOST must be configured")
    if not EMAIL_HOST_PASSWORD or EMAIL_HOST_PASSWORD.lower().startswith("change-me"):
        errors.append("CAREERHUB_EMAIL_HOST_PASSWORD must be configured")
    if errors:
        raise ImproperlyConfigured(
            "CareerHub security check failed: " + "; ".join(errors)
        )

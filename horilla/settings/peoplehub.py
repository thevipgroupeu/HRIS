"""Restricted private runtime for people.thevipgroup.eu."""

import os
from copy import deepcopy

from . import *  # noqa: F403

ROOT_URLCONF = "horilla.peoplehub_urls"
WSGI_APPLICATION = "horilla.peoplehub_wsgi.application"
ALLOWED_HOSTS = [
    value.strip()
    for value in os.getenv(
        "PEOPLEHUB_ALLOWED_HOSTS", "people.thevipgroup.eu,localhost,127.0.0.1"
    ).split(",")
    if value.strip()
]
CSRF_TRUSTED_ORIGINS = [
    value.strip()
    for value in os.getenv(
        "PEOPLEHUB_CSRF_TRUSTED_ORIGINS", "https://people.thevipgroup.eu"
    ).split(",")
    if value.strip()
]
SESSION_COOKIE_NAME = "tvg_peoplehub_sessionid"
CSRF_COOKIE_NAME = "tvg_peoplehub_csrftoken"
# Do not share employee cookies with the sibling public candidate origin.
SESSION_COOKIE_DOMAIN = None
CSRF_COOKIE_DOMAIN = None
if IS_PRODUCTION:  # noqa: F405
    from .security import validate_production_secrets

    validate_production_secrets(
        SECRET_KEY, ALLOWED_HOSTS, DB_INIT_PASSWORD
    )  # noqa: F405
LOGIN_URL = "peoplehub:login"
LOGIN_REDIRECT_URL = "peoplehub:dashboard"
LOGOUT_REDIRECT_URL = "peoplehub:login"

# Keep Django's safe 405 response (including Allow); the native replacement
# renders the full HR template context, which this restricted URL tree lacks.
MIDDLEWARE = [
    item
    for item in MIDDLEWARE
    if item != "horilla.horilla_middlewares.MethodNotAllowedMiddleware"
]  # noqa: F405

# The portal templates provide their own narrow context. Horilla's global menu
# processor assumes the full HR URL tree and could disclose navigation metadata.
TEMPLATES = deepcopy(TEMPLATES)  # noqa: F405
TEMPLATES[0]["OPTIONS"]["context_processors"] = [
    "django.template.context_processors.request",
    "django.contrib.auth.context_processors.auth",
    "django.contrib.messages.context_processors.messages",
]

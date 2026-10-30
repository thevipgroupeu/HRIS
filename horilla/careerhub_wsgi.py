"""WSGI entry point for the isolated public CareerHub service."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "horilla.settings.careerhub")

application = get_wsgi_application()

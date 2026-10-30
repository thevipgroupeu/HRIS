import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "horilla.settings.peoplehub")

application = get_wsgi_application()

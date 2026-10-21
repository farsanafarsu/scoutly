"""WSGI entry point, e.g. `gunicorn config.wsgi --timeout 120` (audits can take up to a minute)."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_wsgi_application()

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lexinform_web.config.production")

application = get_wsgi_application()

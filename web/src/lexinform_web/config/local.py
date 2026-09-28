from .base import *  # noqa: F403

SECRET_KEY = "lexinform-web-local-only"  # noqa: F405
DEBUG = True  # noqa: F405
WEBSITE_DEMO_ENABLED = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]  # noqa: F405
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}

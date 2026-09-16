import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ImproperlyConfigured(f"{name} is required")
    return value


SECRET_KEY = _required("LEXINFORM_WEB_SECRET_KEY")  # noqa: F405
ALLOWED_HOSTS = [host.strip() for host in _required("LEXINFORM_WEB_ALLOWED_HOSTS").split(",")]
DATABASES["default"].update(  # noqa: F405
    {
        "NAME": _required("LEXINFORM_WEB_DB_NAME"),
        "USER": _required("LEXINFORM_WEB_DB_USER"),
        "PASSWORD": _required("LEXINFORM_WEB_DB_PASSWORD"),
        "HOST": _required("LEXINFORM_WEB_DB_HOST"),
        "PORT": os.environ.get("LEXINFORM_WEB_DB_PORT", "5432"),
    }
)
WAGTAILADMIN_BASE_URL = _required("LEXINFORM_WEB_BASE_URL")  # noqa: F405

CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True

import os
from pathlib import Path
from typing import Any

from lexinform_web.languages import SUPPORTED_LANGUAGES

BASE_DIR = Path(__file__).resolve().parents[3]

INSTALLED_APPS = [
    "lexinform_web.accounts",
    "lexinform_web.editorial",
    "lexinform_web.operations",
    "django_tasks_db",
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "wagtail.contrib.settings",
    "wagtail.contrib.redirects",
    "wagtail.contrib.sitemaps",
    "wagtail.contrib.simple_translation",
    "wagtail.embeds",
    "wagtail.sites",
    "wagtail.users",
    "wagtail.snippets",
    "wagtail.documents",
    "wagtail.images",
    "wagtail.search",
    "wagtail.admin",
    "wagtail",
    "modelcluster",
    "taggit",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.postgres",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "lexinform_web.accounts.middleware.StaffMFAMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "wagtail.contrib.redirects.middleware.RedirectMiddleware",
]

ROOT_URLCONF = "lexinform_web.config.urls"
WSGI_APPLICATION = "lexinform_web.config.wsgi.application"

TEMPLATES: list[dict[str, Any]] = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "src" / "lexinform_web" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("LEXINFORM_WEB_DB_NAME", "lexinform_web"),
        "USER": os.environ.get("LEXINFORM_WEB_DB_USER", "lexinform_web"),
        "PASSWORD": os.environ.get("LEXINFORM_WEB_DB_PASSWORD", "lexinform_web"),
        "HOST": os.environ.get("LEXINFORM_WEB_DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("LEXINFORM_WEB_DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["allauth.account.auth_backends.AuthenticationBackend"]
ACCOUNT_ADAPTER = "lexinform_web.accounts.adapters.StaffAccountAdapter"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_LOGIN_ON_PASSWORD_RESET = False
ACCOUNT_REAUTHENTICATION_REQUIRED = True
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]
MFA_TOTP_ISSUER = "lexinform"
MFA_ADAPTER = "lexinform_web.accounts.adapters.StaffMFAAdapter"
LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "/admin/"
LOGOUT_REDIRECT_URL = "account_login"
WAGTAILADMIN_LOGIN_URL = "/accounts/login/"
STAFF_MFA_MAX_AGE_SECONDS = 12 * 60 * 60
TASKS = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend", "QUEUES": ["default"]}}
WORKER_LOCK_PATH = os.environ.get("LEXINFORM_WEB_WORKER_LOCK_PATH", str(BASE_DIR / ".worker.lock"))

LANGUAGE_CODE = "pl"
LANGUAGES = list(SUPPORTED_LANGUAGES)
WAGTAIL_CONTENT_LANGUAGES = LANGUAGES
WAGTAIL_I18N_ENABLED = True
LOCALE_PATHS = [BASE_DIR / "src" / "lexinform_web" / "locale"]

TIME_ZONE = "Europe/Warsaw"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
WAGTAIL_SITE_NAME = "lexinform"
WAGTAIL_PAGE_MODEL = "wagtailcore.Page"
WAGTAILADMIN_BASE_URL = "http://localhost:8000"

SECRET_KEY = ""
DEBUG = False
ALLOWED_HOSTS: list[str] = []

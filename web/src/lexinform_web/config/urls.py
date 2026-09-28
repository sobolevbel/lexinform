from django.conf.urls.i18n import i18n_patterns
from django.urls import include, path
from wagtail import urls as wagtail_urls
from wagtail.admin import urls as wagtailadmin_urls
from wagtail.documents import urls as wagtaildocs_urls

from lexinform_web import views

urlpatterns = [
    path("", views.language_home),
    path("__components__/", views.components_gallery, name="components_gallery"),
    path("accounts/", include("allauth.urls")),
    path("admin/", include(wagtailadmin_urls)),
    path("documents/", include(wagtaildocs_urls)),
    path("i18n/", include("django.conf.urls.i18n")),
]

urlpatterns += i18n_patterns(
    path("", views.home, name="home"),
    path("", include(wagtail_urls)),
)

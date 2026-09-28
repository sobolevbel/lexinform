import pytest
from bs4 import BeautifulSoup
from django.contrib.staticfiles import finders
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils.translation import override
from django.views.defaults import server_error
from wagtail.models import Locale, Page, Site

from lexinform_web.editorial.models import GuidePage
from lexinform_web.languages import SUPPORTED_LANGUAGES


@pytest.mark.parametrize("language", [code for code, _ in SUPPORTED_LANGUAGES])
def test_language_homes_work_without_javascript(client: Client, language: str) -> None:
    response = client.get(f"/{language}/")

    assert response.status_code == 200
    document = BeautifulSoup(response.content, "html.parser")
    assert document.select_one('html[lang="ru"]') is not None
    assert document.select_one('meta[name="robots"][content="noindex,nofollow"]')
    assert document.select_one('a[href="#main"]') is not None
    assert document.select_one('main#main[tabindex="-1"]') is not None
    assert len(document.select("h1")) == 1
    assert document.select_one("script") is None
    assert {link.get("href") for link in document.select("details a")} == {
        f"/{code}/" for code, _ in SUPPORTED_LANGUAGES
    }
    selected = document.select_one('details a[aria-current="true"]')
    assert selected is not None
    assert selected.get("href") == f"/{language}/"
    assert "Публичных карточек здесь пока нет" in document.get_text()
    assert client.head(f"/{language}/").status_code == 200
    assert client.post(f"/{language}/").status_code == 405


def test_root_redirects_to_language_without_localizing_staff_routes(client: Client) -> None:
    response = client.get("/", HTTP_ACCEPT_LANGUAGE="uk")

    assert response.status_code == 302
    assert response["Location"] == "/uk/"
    with override("uk"):
        assert reverse("home") == "/uk/"
        assert reverse("wagtailadmin_home") == "/admin/"
        assert reverse("account_login") == "/accounts/login/"


def test_server_error_renders_without_database_or_request_context() -> None:
    response = server_error(RequestFactory().get("/ru/"))

    assert response.status_code == 500
    assert "Не удалось открыть страницу" in response.content.decode()
    assert b"noindex,nofollow" in response.content
    assert finders.find("lexinform_web/site.css") is not None


@pytest.mark.django_db
def test_missing_page_is_not_a_matter_outcome(client: Client) -> None:
    response = client.get("/ru/missing-page/")

    assert response.status_code == 404
    assert "не означает, что законопроект отозван" in response.content.decode()
    assert b'href="/ru/"' in response.content


@pytest.mark.django_db
def test_localized_cms_route_serves_live_content_and_preview_uses_shell(client: Client) -> None:
    site = Site.objects.get(is_default_site=True)
    parent = Page.objects.get(pk=site.root_page_id)
    polish = Locale.objects.get(language_code="pl")
    page = GuidePage(
        title="Jak przygotować opinię",
        slug="opinia",
        locale=polish,
        summary="Opublikowana treść.",
    )
    parent.add_child(instance=page)
    page.save_revision().publish()
    page.summary = "Nieopublikowana poprawka."
    page.save_revision()

    response = client.get("/pl/opinia/")

    assert response.status_code == 200
    assert "Opublikowana treść." in response.content.decode()
    assert "Nieopublikowana poprawka." not in response.content.decode()
    assert b'<article class="reading" lang="pl">' in response.content
    preview = page.serve_preview(RequestFactory().get("/admin/preview/"), "default")
    preview.render()
    assert "Nieopublikowana poprawka." in preview.content.decode()
    assert b"noindex,nofollow" in preview.content
    assert b"lexinform_web/site.css" in preview.content

from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.test import Client, RequestFactory, override_settings
from wagtail.models import Page, Site

from lexinform_web.editorial.demo import DEMO_KEY, DEMO_SLUG, seed_demo_guide
from lexinform_web.editorial.models import GuidePage

pytestmark = pytest.mark.django_db


@override_settings(WEBSITE_DEMO_ENABLED=True)
def test_demo_is_unpublished_and_repeat_preserves_editorial_revision(client: Client) -> None:
    page, created = seed_demo_guide()
    assert created
    assert not page.live
    assert client.get(f"/ru/{DEMO_SLUG}/").status_code == 404

    preview = page.serve_preview(RequestFactory().get("/preview/"), "default")
    preview.render()
    assert "Открытый срок участия не подтверждён" in preview.content.decode()
    assert 'lang="uk"' in preview.content.decode()
    assert b"noindex,nofollow" in preview.content

    page.summary = "Editorial correction"
    page.save_revision().publish()
    result, created = seed_demo_guide()
    assert not created
    assert result.pk == page.pk
    assert result.summary == "Editorial correction"
    assert result.live
    assert GuidePage.objects.filter(translation_key=DEMO_KEY).count() == 1


def test_demo_command_is_disabled_by_default() -> None:
    before = Page.objects.count()
    with pytest.raises(CommandError, match="disabled"):
        call_command("seed_demo")
    assert Page.objects.count() == before


@override_settings(WEBSITE_DEMO_ENABLED=True)
def test_demo_command_reports_existing_draft() -> None:
    output = StringIO()
    call_command("seed_demo", stdout=output)
    call_command("seed_demo", stdout=output)
    assert "Created draft" in output.getvalue()
    assert "Kept existing content" in output.getvalue()


@override_settings(WEBSITE_DEMO_ENABLED=True)
def test_demo_does_not_replace_an_unrelated_slug() -> None:
    site = Site.objects.get(is_default_site=True)
    parent = Page.objects.get(pk=site.root_page_id)
    parent.add_child(instance=GuidePage(title="Existing guide", slug=DEMO_SLUG))
    with pytest.raises(CommandError, match="already occupied"):
        call_command("seed_demo")
    assert not GuidePage.objects.filter(translation_key=DEMO_KEY).exists()

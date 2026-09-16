import pytest
from django.test import RequestFactory
from wagtail.models import Locale, Page

from lexinform_web.editorial.models import GuidePage, Topic

pytestmark = pytest.mark.django_db


def test_guide_page_publishes_structured_content() -> None:
    root = Page.get_first_root_node()
    assert root is not None
    page = GuidePage(
        title="How to submit an opinion",
        slug="submit-an-opinion",
        summary="A practical guide.",
        body=[
            ("paragraph", "Send the opinion before the deadline."),
            ("callout", {"title": "Language", "body": "Write in Polish."}),
        ],
    )
    root.add_child(instance=page)

    page.save_revision().publish()

    published = GuidePage.objects.get(pk=page.pk)
    assert published.live is True
    assert [block.block_type for block in published.body] == ["paragraph", "callout"]


def test_topic_translation_keeps_identity_and_draft_preserves_live_text() -> None:
    polish = Locale.objects.get(language_code="pl")
    english, _ = Locale.objects.get_or_create(language_code="en")
    topic = Topic.objects.create(
        locale=polish,
        name="Legalizacja pobytu",
        slug="legalizacja-pobytu",
        description="Published explanation.",
    )
    topic.save_revision().publish()

    translation = topic.copy_for_translation(english)
    translation.name = "Legal stay"
    translation.slug = "legal-stay"
    translation.save()

    assert translation.translation_key == topic.translation_key
    assert translation.locale == english

    topic.description = "Draft correction."
    draft = topic.save_revision()
    live = Topic.objects.get(pk=topic.pk)
    assert live.description == "Published explanation."

    response = topic.serve_preview(RequestFactory().get("/preview/"), "default")
    response.render()
    assert response.status_code == 200
    assert b"Draft correction." in response.content

    draft.publish()
    topic.refresh_from_db()
    assert topic.description == "Draft correction."

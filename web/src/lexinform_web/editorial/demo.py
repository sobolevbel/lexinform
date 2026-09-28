from uuid import UUID

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from wagtail.models import Locale, Page, Site

from lexinform_web.editorial.models import GuidePage

DEMO_KEY = UUID("25045c46-c044-40df-b6a9-4b0a7817dd49")
DEMO_SLUG = "demo-reading"


@transaction.atomic
def seed_demo_guide() -> tuple[GuidePage, bool]:
    if not settings.WEBSITE_DEMO_ENABLED:
        raise ImproperlyConfigured("Demo content is disabled in these settings.")

    site = Site.objects.select_for_update().get(is_default_site=True)
    existing = GuidePage.objects.filter(translation_key=DEMO_KEY).first()
    if existing is not None:
        return existing, False
    parent = Page.objects.select_for_update().get(pk=site.root_page_id)
    if parent.get_children().filter(slug=DEMO_SLUG).exists():
        raise ValueError("The demo-reading slug is already occupied; no content was changed.")

    locale, _ = Locale.objects.get_or_create(language_code="ru")
    page = GuidePage(
        title="Демонстрация: Rządowy projekt ustawy o zmianie ustawy o cudzoziemcach "
        "oraz niektórych innych ustaw",
        slug=DEMO_SLUG,
        translation_key=DEMO_KEY,
        locale=locale,
        live=False,
        summary="Учебный макет для проверки чтения. Это не действующий гайд и не законопроект.",
        body=[
            (
                "callout",
                {
                    "title": "Актуальность не подтверждена",
                    "body": "<p>Демонстрационные данные. Открытый срок участия не подтверждён. "
                    "Эта страница не предлагает отправлять обращения.</p>",
                },
            ),
            (
                "paragraph",
                "<p>Проверка русского текста: проживание, работа, семья. "
                "<b>Важное пояснение</b> и <i>короткая цитата</i>.</p>"
                '<p lang="pl">Polski: Zażółć gęślą jaźń. Źródło i treść projektu.</p>'
                '<p lang="en">English: Residence, employment and family.</p>'
                '<p lang="be">Беларуская: правы, сям’я, жыццё ў Польшчы.</p>'
                '<p lang="uk">Українська: права, сім’я, проживання, їжа, ґрунт.</p>',
            ),
        ],
    )
    parent.add_child(instance=page)
    page.save_revision()
    return page, True

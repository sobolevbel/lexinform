from typing import ClassVar

from django.db import models
from django.http import HttpRequest
from wagtail.admin.panels import FieldPanel, PublishingPanel
from wagtail.fields import StreamField
from wagtail.models import (
    DraftStateMixin,
    Page,
    PreviewableMixin,
    RevisionMixin,
    TranslatableMixin,
)
from wagtail.snippets.models import register_snippet

from lexinform_web.editorial.blocks import GUIDE_BLOCKS


class GuidePage(Page):
    objects: ClassVar[models.Manager[GuidePage]]
    summary = models.TextField(blank=True)
    body = StreamField(GUIDE_BLOCKS, blank=True, use_json_field=True)

    content_panels = Page.content_panels + [FieldPanel("summary"), FieldPanel("body")]
    subpage_types: ClassVar[list[str]] = []


@register_snippet
class Topic(
    DraftStateMixin,
    RevisionMixin,
    PreviewableMixin,
    TranslatableMixin,
    models.Model,
):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=120)
    description = models.TextField(blank=True)

    panels = [
        FieldPanel("name"),
        FieldPanel("slug"),
        FieldPanel("description"),
        PublishingPanel(),
    ]

    class Meta(TranslatableMixin.Meta):
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=("locale", "slug"), name="editorial_topic_locale_slug")
        ]

    def __str__(self) -> str:
        return self.name

    def get_preview_template(self, request: HttpRequest, mode_name: str) -> str:
        return "editorial/topic_preview.html"

    def get_preview_context(self, request: HttpRequest, mode_name: str) -> dict[str, object]:
        context = super().get_preview_context(request, mode_name)
        context["topic"] = self
        return context

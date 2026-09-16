from typing import Any, ClassVar
from uuid import UUID

from django.db import models
from django.http import HttpRequest
from django.template.response import TemplateResponse

class Locale(models.Model):
    objects: ClassVar[models.Manager[Locale]]
    language_code: str

class Revision(models.Model):
    def publish(self) -> Any: ...

class Page(models.Model):
    objects: ClassVar[models.Manager[Page]]
    content_panels: ClassVar[list[Any]]
    subpage_types: ClassVar[list[str]]
    title: str
    slug: str
    live: bool
    aliases: models.Manager[Page]
    locale: Locale
    translation_key: UUID
    @classmethod
    def get_first_root_node(cls) -> Page | None: ...
    def add_child(self, *, instance: Page) -> Page: ...
    def save_revision(self, **kwargs: Any) -> Revision: ...
    def copy_for_translation(
        self, locale: Locale, copy_parents: bool = ..., alias: bool = ...
    ) -> Page: ...

class DraftStateMixin(models.Model):
    live: bool
    has_unpublished_changes: bool

class RevisionMixin(models.Model):
    def save_revision(self, **kwargs: Any) -> Revision: ...

class PreviewableMixin:
    def get_preview_template(self, request: HttpRequest, mode_name: str) -> str: ...
    def get_preview_context(self, request: HttpRequest, mode_name: str) -> dict[str, object]: ...
    def serve_preview(self, request: HttpRequest, mode_name: str) -> TemplateResponse: ...

class TranslatableMixin(models.Model):
    locale: Locale
    translation_key: UUID
    class Meta: ...

    def copy_for_translation(
        self, locale: Locale, exclude_fields: list[str] | None = ...
    ) -> Any: ...

import uuid

import django.db.models.deletion
import wagtail.fields
import wagtail.models.preview
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("wagtailcore", "0098_apitoken"),
    ]

    operations = [
        migrations.CreateModel(
            name="GuidePage",
            fields=[
                (
                    "page_ptr",
                    models.OneToOneField(
                        auto_created=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        parent_link=True,
                        primary_key=True,
                        serialize=False,
                        to=settings.WAGTAIL_PAGE_MODEL,
                    ),
                ),
                ("summary", models.TextField(blank=True)),
                (
                    "body",
                    wagtail.fields.StreamField(
                        [("paragraph", 0), ("callout", 2)],
                        blank=True,
                        block_lookup={
                            0: (
                                "wagtail.blocks.RichTextBlock",
                                (),
                                {"features": ["bold", "italic", "link"]},
                            ),
                            1: ("wagtail.blocks.CharBlock", (), {"required": False}),
                            2: (
                                "wagtail.blocks.StructBlock",
                                [[("title", 1), ("body", 0)]],
                                {},
                            ),
                        },
                    ),
                ),
            ],
            options={"abstract": False},
            bases=("wagtailcore.page",),
        ),
        migrations.CreateModel(
            name="Topic",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("translation_key", models.UUIDField(default=uuid.uuid4, editable=False)),
                (
                    "live",
                    models.BooleanField(default=True, editable=False, verbose_name="live"),
                ),
                (
                    "has_unpublished_changes",
                    models.BooleanField(
                        default=False, editable=False, verbose_name="has unpublished changes"
                    ),
                ),
                (
                    "first_published_at",
                    models.DateTimeField(
                        blank=True,
                        db_index=True,
                        null=True,
                        verbose_name="first published at",
                    ),
                ),
                (
                    "last_published_at",
                    models.DateTimeField(
                        editable=False, null=True, verbose_name="last published at"
                    ),
                ),
                (
                    "go_live_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="go live date/time"),
                ),
                (
                    "expire_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="expiry date/time"),
                ),
                (
                    "expired",
                    models.BooleanField(default=False, editable=False, verbose_name="expired"),
                ),
                ("name", models.CharField(max_length=120)),
                ("slug", models.SlugField(max_length=120)),
                ("description", models.TextField(blank=True)),
                (
                    "latest_revision",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="wagtailcore.revision",
                        verbose_name="latest revision",
                    ),
                ),
                (
                    "live_revision",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="wagtailcore.revision",
                        verbose_name="live revision",
                    ),
                ),
                (
                    "locale",
                    models.ForeignKey(
                        editable=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="wagtailcore.locale",
                        verbose_name="locale",
                    ),
                ),
            ],
            options={
                "ordering": ["name"],
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        fields=("locale", "slug"), name="editorial_topic_locale_slug"
                    )
                ],
                "unique_together": {("translation_key", "locale")},
            },
            bases=(wagtail.models.preview.PreviewableMixin, models.Model),
        ),
    ]

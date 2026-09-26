import uuid

from django.conf import settings
from django.db import models


class Matter(models.Model):
    class Visibility(models.TextChoices):
        DRAFT = "draft", "Черновик"
        PUBLIC = "public", "Опубликовано"
        HIDDEN = "hidden", "Скрыто"

    public_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    visibility = models.CharField(max_length=8, choices=Visibility, default=Visibility.DRAFT)
    canonical = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="merged_matters"
    )
    created_at = models.DateTimeField()

    class Meta:
        permissions = [("reconcile_matter", "Can merge and split matter identities")]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(canonical=models.F("public_id")),
                name="matter_not_own_canonical",
            ),
            models.CheckConstraint(
                condition=models.Q(visibility__in=["draft", "public", "hidden"]),
                name="matter_valid_visibility",
            ),
        ]

    def get_absolute_url(self) -> str:
        return f"/b/{self.public_id}"


class SourceIdentity(models.Model):
    class Source(models.TextChoices):
        SEJM = "sejm", "Сейм"
        RPW = "rpw", "RPW"
        RCL = "rcl", "RCL"
        WYKAZ = "wykaz", "Wykaz"

    source = models.CharField(max_length=8, choices=Source)
    scope = models.CharField(max_length=100)
    external_id = models.CharField(max_length=255)
    matter = models.ForeignKey(Matter, on_delete=models.PROTECT, related_name="identities")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "scope", "external_id"], name="source_identity_natural_key"
            ),
            models.CheckConstraint(
                condition=~models.Q(scope="") & ~models.Q(external_id=""),
                name="source_identity_nonempty_key",
            ),
            models.CheckConstraint(
                condition=models.Q(source__in=["sejm", "rpw", "rcl", "wykaz"]),
                name="source_identity_valid_source",
            ),
        ]


class MatterDecision(models.Model):
    class Kind(models.TextChoices):
        MERGE = "merge", "Объединение"
        SPLIT = "split", "Разделение"

    kind = models.CharField(max_length=8, choices=Kind)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reason = models.TextField()
    before = models.JSONField()
    after = models.JSONField()
    created_at = models.DateTimeField()


class MatterRelation(models.Model):
    class Kind(models.TextChoices):
        CONTINUATION = "continuation", "Продолжение"
        JOINT = "joint", "Совместное рассмотрение"
        ALTERNATIVE = "alternative", "Альтернативная инициатива"

    from_matter = models.ForeignKey(
        Matter, on_delete=models.PROTECT, related_name="outgoing_relations"
    )
    to_matter = models.ForeignKey(
        Matter, on_delete=models.PROTECT, related_name="incoming_relations"
    )
    kind = models.CharField(max_length=16, choices=Kind)
    evidence_url = models.URLField(max_length=2048)
    decision = models.ForeignKey(MatterDecision, null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["from_matter", "to_matter", "kind"], name="matter_relation_unique"
            ),
            models.CheckConstraint(
                condition=~models.Q(from_matter=models.F("to_matter")),
                name="matter_relation_not_self",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=["continuation", "joint", "alternative"]),
                name="matter_relation_valid_kind",
            ),
        ]


class PublicAlias(models.Model):
    path = models.CharField(max_length=2048, unique=True)
    matter = models.ForeignKey(Matter, on_delete=models.PROTECT, related_name="public_aliases")

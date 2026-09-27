from django.db import models

from lexinform_web.matters.models import Matter, SourceIdentity


class ImportGeneration(models.Model):
    """One accepted snapshot; a generation row exists only if its activation committed."""

    source_commit = models.CharField(max_length=40)
    dump_sha256 = models.CharField(max_length=64)
    source_schema = models.PositiveIntegerField()
    contract_version = models.PositiveIntegerField()
    created_at = models.DateTimeField()
    counts = models.JSONField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_commit", "dump_sha256"], name="import_generation_snapshot"
            )
        ]


class ActiveImport(models.Model):
    generation = models.ForeignKey(ImportGeneration, null=True, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(pk=1), name="active_import_singleton")
        ]


class SourceSnapshot(models.Model):
    generation = models.ForeignKey(
        ImportGeneration, on_delete=models.CASCADE, related_name="snapshots"
    )
    identity = models.ForeignKey(SourceIdentity, on_delete=models.PROTECT, related_name="+")
    payload = models.JSONField()
    semantic_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["generation", "identity"], name="source_snapshot_per_generation"
            )
        ]


class MatterFacts(models.Model):
    generation = models.ForeignKey(ImportGeneration, on_delete=models.CASCADE, related_name="facts")
    matter = models.ForeignKey(Matter, on_delete=models.PROTECT, related_name="facts")
    snapshot = models.ForeignKey(SourceSnapshot, on_delete=models.CASCADE, related_name="+")
    title = models.TextField()
    bot_status = models.CharField(max_length=32)
    relevant = models.BooleanField(null=True)
    score = models.PositiveSmallIntegerField(null=True)
    category = models.CharField(max_length=32, blank=True)
    document_date = models.DateField(null=True)
    closure_date = models.DateField(null=True)
    content_updated_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["generation", "matter"], name="matter_facts_unique")
        ]


class MatterEvent(models.Model):
    event_key = models.CharField(max_length=255, unique=True)
    matter = models.ForeignKey(Matter, on_delete=models.PROTECT, related_name="events")
    identity = models.ForeignKey(SourceIdentity, on_delete=models.PROTECT, related_name="+")
    provenance = models.CharField(max_length=32)
    observed_at = models.DateTimeField()
    occurred_at = models.DateTimeField(null=True)


class EventRevision(models.Model):
    event = models.ForeignKey(MatterEvent, on_delete=models.PROTECT, related_name="revisions")
    generation = models.ForeignKey(ImportGeneration, on_delete=models.PROTECT, related_name="+")
    content = models.JSONField()
    content_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["event", "generation"], name="event_revision_unique")
        ]

from uuid import UUID

from django.db.models import OuterRef, Subquery

from lexinform_web.ingestion.models import (
    ActiveImport,
    EventRevision,
    ImportGeneration,
    MatterEvent,
    MatterFacts,
)


def active_generation() -> ImportGeneration | None:
    """Read once per request; every later read takes this generation, never the pointer again."""
    active = ActiveImport.objects.select_related("generation").filter(pk=1).first()
    return active.generation if active else None


def facts_at(generation: ImportGeneration, matter: UUID) -> MatterFacts | None:
    return (
        MatterFacts.objects.filter(generation=generation, matter_id=matter)
        .select_related("snapshot")
        .first()
    )


def events_at(generation: ImportGeneration, matter: UUID) -> list[EventRevision]:
    """The newest revision of each event of the matter as of this generation, oldest event first."""
    newest = (
        EventRevision.objects.filter(event=OuterRef("pk"), generation_id__lte=generation.pk)
        .order_by("-generation_id")
        .values("pk")[:1]
    )
    revisions = (
        MatterEvent.objects.filter(matter_id=matter)
        .annotate(revision=Subquery(newest))
        .filter(revision__isnull=False)
        .order_by("observed_at", "pk")
        .values_list("revision", flat=True)
    )
    ids = list(revisions)
    by_id = EventRevision.objects.select_related("event").in_bulk(ids)
    return [by_id[pk] for pk in ids]

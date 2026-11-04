from datetime import datetime

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.content_sources import SOURCE_CONTEXT_INVALID
from lexinform.models import (
    DeliveryPlan,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
    StatusChange,
)


def queued(
    repo: SqliteBillRepository,
    process: ProcessDetail,
    now: datetime,
    *,
    error: str | None = SOURCE_CONTEXT_INVALID,
    rendered: bool = True,
    ref: str = "blocked",
    channel: str = "@test",
) -> Publication:
    bill = repo.upsert_summary(process, now=now)
    change = StatusChange(
        term=bill.term,
        number=bill.number,
        old_fingerprint=None,
        new_fingerprint=ref,
        new_stages=[],
        detected_at=now,
    )
    change_id = repo.add_status_change(change)
    assert change_id is not None, "each queued fixture has its own recorded status change"
    publication = Publication(
        term=bill.term,
        number=bill.number,
        kind=PublicationKind.STATUS_UPDATE,
        status=PublicationStatus.QUEUED,
        channel_id=channel,
        created_at=now,
        ref=ref,
        error=error,
        status_change_id=change_id,
        delivery=DeliveryPlan(
            bill_json=bill.model_dump_json(),
            change_json=change.model_copy(update={"id": change_id}).model_dump_json(),
            rendered_text="Frozen original" if rendered else "",
        ),
    )
    identity = repo.create_publication(publication)
    saved = repo.publication_by_id(identity)
    assert saved is not None, "the public repository persists the queued fixture"
    return saved

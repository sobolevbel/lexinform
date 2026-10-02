import logging
from collections.abc import Callable
from datetime import datetime

from lexinform.errors import ServiceUnavailableError
from lexinform.models import Publication, PublicationKind, PublicationStatus
from lexinform.ports import BillRepository, Clock, PublishResult

log = logging.getLogger(__name__)


def send_publication(
    repo: BillRepository,
    clock: Clock,
    pub_id: int,
    send: Callable[[], PublishResult | int],
) -> int | None:
    """The caller persists pending work before sending; only definite failures spend attempts."""
    try:
        sent = send()
    except ServiceUnavailableError as exc:
        repo.mark_publication(
            pub_id, PublicationStatus.FAILED, error=exc.describe(), count_attempt=False
        )
        raise
    except Exception as exc:
        log.exception("publication %s failed: %s", pub_id, exc)
        repo.mark_publication(
            pub_id, PublicationStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
        )
        return None
    message_id = sent if isinstance(sent, int) else sent.message_id
    documents = None if isinstance(sent, int) else list(sent.document_message_ids)
    with repo.atomic():
        repo.mark_publication(
            pub_id,
            PublicationStatus.SENT,
            message_id=message_id,
            document_message_ids=documents,
            sent_at=clock.now(),
        )
        publication = repo.publication_by_id(pub_id)
        assert publication is not None, "pending work is persisted before send"
        complete_delivery(repo, publication, message_id=message_id, sent_at=clock.now())
    return message_id


def complete_delivery(
    repo: BillRepository, publication: Publication, *, message_id: int, sent_at: datetime
) -> None:
    """Apply the saved message's effects inside the send or operator-confirmation transaction."""
    plan = publication.delivery
    if plan is None:
        return
    repo.release_planned_changes(
        plan.held_change_ids, publication.channel_id, message_id=message_id, sent_at=sent_at
    )
    if publication.kind is not PublicationKind.STATUS_UPDATE or not plan.with_act:
        return
    notice = repo.get_publication(
        publication.term, publication.number, PublicationKind.ACT_PUBLISHED, publication.channel_id
    )
    if notice is not None and notice.status not in (
        PublicationStatus.QUEUED,
        PublicationStatus.FAILED,
    ):
        return
    identity = (
        notice.id
        if notice is not None and notice.id is not None
        else repo.create_publication(
            Publication(
                term=publication.term,
                number=publication.number,
                kind=PublicationKind.ACT_PUBLISHED,
                status=PublicationStatus.SENT,
                channel_id=publication.channel_id,
                created_at=sent_at,
            )
        )
    )
    repo.mark_publication(identity, PublicationStatus.SENT, message_id=message_id, sent_at=sent_at)


def inherit_card(
    repo: BillRepository,
    *,
    term: int,
    number: str,
    channel_id: str,
    card: Publication,
    now: datetime,
) -> Publication:
    alias = Publication(
        term=term,
        number=number,
        kind=PublicationKind.NEW_BILL,
        status=PublicationStatus.SENT,
        channel_id=channel_id,
        message_id=card.message_id,
        created_at=now,
        sent_at=card.sent_at,
    )
    alias.id = repo.create_publication(alias)
    # An existing row keeps its message metadata during create_publication's upsert.
    repo.mark_publication(
        alias.id, PublicationStatus.SENT, message_id=card.message_id, sent_at=card.sent_at
    )
    return alias

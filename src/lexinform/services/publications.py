import logging
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime

from lexinform.errors import ServiceUnavailableError
from lexinform.models import Publication, PublicationKind, PublicationStatus
from lexinform.ports import BillRepository, Clock, PublishResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HeldRelease:
    channel_id: str
    change_ids: tuple[int, ...]


def send_publication(
    repo: BillRepository,
    clock: Clock,
    pub_id: int,
    send: Callable[[], PublishResult | int],
    *,
    release: HeldRelease | None = None,
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
    with repo.atomic() if release is not None else nullcontext():
        repo.mark_publication(
            pub_id,
            PublicationStatus.SENT,
            message_id=message_id,
            document_message_ids=documents,
            sent_at=clock.now(),
        )
        if release is not None:
            repo.release_planned_changes(
                release.change_ids,
                release.channel_id,
                message_id=message_id,
                sent_at=clock.now(),
            )
    return message_id


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

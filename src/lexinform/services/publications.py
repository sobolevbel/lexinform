from datetime import datetime

from lexinform.models import Publication, PublicationKind, PublicationStatus
from lexinform.ports import BillRepository


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

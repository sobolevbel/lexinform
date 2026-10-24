from collections.abc import Callable
from datetime import date
from functools import partial
from zoneinfo import ZoneInfo

from lexinform.models import (
    Bill,
    DeliveryPlan,
    PrintInfo,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.ports import BillRepository, Clock, Publisher, StateCheckpoint
from lexinform.services.message_parts import continuation_snapshot
from lexinform.services.publications import (
    card_revision_owner,
    open_card_edit_ids,
    send_publication,
)


def edit_single_card(
    repo: BillRepository,
    clock: Clock,
    publisher: Publisher,
    bill: Bill,
    card: Publication,
    print_info: PrintInfo | None,
    *,
    channel_id: str,
    checkpoint: StateCheckpoint | None = None,
    local_tz: ZoneInfo = ZoneInfo("Europe/Warsaw"),
    digest: str | None = None,
    today: date | None = None,
    load_print: Callable[[], PrintInfo | None] | None = None,
    delivery_attempts: set[int] | None = None,
) -> bool | None:
    """None preserves the legacy multipart path; False never authorizes another physical edit."""
    owner = repo.publication_by_id(card.id) if card.id is not None else None
    if (
        bill.analysis is None
        or owner is None
        or owner.kind is not PublicationKind.NEW_BILL
        or owner.status is not PublicationStatus.SENT
        or owner.term != bill.term
        or owner.number != bill.number
        or owner.channel_id != channel_id
        or owner.message_id is None
        or owner.message_id != card.message_id
    ):
        return False
    if open_card_edit_ids(repo, owner):
        return False
    assert owner.id is not None, "a stored delivered owner has its publication identity"
    current = repo.card_refresh_parts(owner.id)
    if not current:
        return False
    physical = [
        repo.card_refresh_parts(alias.id)
        for alias in repo.card_aliases(channel_id, owner.message_id)
        if alias.id is not None
    ]
    if any(
        part.kind is PublicationKind.MESSAGE_PART
        and part.status not in (PublicationStatus.SENT, PublicationStatus.DISMISSED)
        for edition in physical
        for part in edition
    ):
        return False
    if any(len(edition) > 1 for edition in physical):
        return None
    today = today or clock.now().astimezone(local_tz).date()
    desired = digest if digest is not None else publisher.card_digest(bill, today=today)
    if desired == owner.rendered_sha256:
        return False
    previous = current[0]
    if dismissed_card_edit(repo, owner, current, desired):
        return False
    if load_print is not None:
        print_info = load_print()
    texts = publisher.render_card_parts(bill, print_info, minimum=1, today=today)
    if len(texts) != 1:
        return None
    ref = f"card/{owner.id}/{desired}-{previous.id}/1"
    existing = repo.get_publication(
        bill.term, bill.number, PublicationKind.MESSAGE_PART, channel_id, ref=ref
    )
    if existing is not None:
        return False
    with repo.atomic():
        identity = repo.create_publication(
            Publication(
                term=bill.term,
                number=bill.number,
                channel_id=channel_id,
                kind=PublicationKind.MESSAGE_PART,
                status=PublicationStatus.QUEUED,
                created_at=clock.now(),
                ref=ref,
                delivery=DeliveryPlan(
                    bill_json=continuation_snapshot(bill),
                    rendered_text=texts[0],
                    edit_message_id=owner.message_id,
                    revision_of=owner.id,
                    today=today,
                    reply_to=owner.message_id,
                    continuation_parent=owner.id,
                    continuation_index=1,
                ),
            )
        )
        repo.set_card_digest(identity, desired)
        repo.mark_publication(identity, PublicationStatus.PENDING, count_attempt=False)
        operation = repo.publication_by_id(identity)
        assert operation is not None, "the immutable operation is saved before its edit"
    if delivery_attempts is not None:
        delivery_attempts.add(identity)
    return (
        send_publication(
            repo,
            clock,
            identity,
            partial(publisher.publish_prepared, operation),
            checkpoint=checkpoint,
        )
        is not None
    )


def dismissed_card_edit(
    repo: BillRepository, card: Publication, parts: list[Publication], digest: str
) -> bool:
    return bool(
        parts
        and parts[0].delivery is not None
        and parts[0].delivery.rendered_text
        and parts[0].rendered_sha256 == digest
        and any(part.status is PublicationStatus.DISMISSED for part in parts)
        and card_revision_owner(repo, parts[0]) == card
    )

import logging
from collections.abc import Callable
from dataclasses import dataclass
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
from lexinform.services.message_parts import continuation_snapshot, send_continuations
from lexinform.services.publications import (
    card_revision_owner,
    open_card_edit_ids,
    send_publication,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CardEditResult:
    completed: bool = False
    new_messages: int = 0
    errors: tuple[str, ...] = ()


def _edition_owner(
    repo: BillRepository, root: Publication, aliases: list[Publication]
) -> Publication | None:
    if root.kind is PublicationKind.NEW_BILL:
        return next((alias for alias in aliases if alias.id == root.id), None)
    if root.kind is not PublicationKind.MESSAGE_PART:
        return None
    if root.delivery is not None:
        if not root.delivery.rendered_text or root.delivery.publication_ids:
            return None
        owner = card_revision_owner(repo, root)
        return (
            owner if owner is not None and any(owner.id == alias.id for alias in aliases) else None
        )
    if root.status is not PublicationStatus.SENT:
        return None
    return next(
        (
            alias
            for alias in aliases
            if (root.term, root.number, root.channel_id, root.message_id)
            == (alias.term, alias.number, alias.channel_id, alias.message_id)
            and (root.ref or "").startswith(f"card/{alias.id}/")
            and len((root.ref or "").split("/")) == 4
            and (root.ref or "").endswith("/1")
        ),
        None,
    )


def _valid_members(edition: list[Publication]) -> bool:
    root = edition[0]
    if root.delivery is not None and root.delivery.continuation_ids != tuple(
        part.id for part in edition[1:]
    ):
        return False
    parent = root.id
    prefix = (
        f"message/{root.id}"
        if root.kind is PublicationKind.NEW_BILL
        else (root.ref or "").rsplit("/", 1)[0]
    )
    for index, part in enumerate(edition[1:], start=2):
        plan = part.delivery
        if (
            part.kind is not PublicationKind.MESSAGE_PART
            or (part.term, part.number, part.channel_id)
            != (root.term, root.number, root.channel_id)
            or part.status not in (PublicationStatus.SENT, PublicationStatus.DISMISSED)
            or (
                plan is not None
                and (
                    plan.continuation_root != root.id
                    or plan.continuation_parent != parent
                    or plan.continuation_index != index
                    or bool(plan.publication_ids)
                )
            )
            or (
                plan is None
                and (part.status is not PublicationStatus.SENT or part.ref != f"{prefix}/{index}")
            )
        ):
            return False
        parent = part.id
    return True


def _physical_edition(repo: BillRepository, aliases: list[Publication]) -> list[Publication]:
    editions: list[list[Publication]] = []
    for alias in aliases:
        if alias.id is None:
            continue
        edition = repo.card_refresh_parts(alias.id)
        if not edition:
            continue
        if _edition_owner(repo, edition[0], aliases) is None:
            if len(edition) > 1 or alias.delivery is None:
                return []
            original = [
                repo.publication_by_id(identity) for identity in alias.delivery.continuation_ids
            ]
            if any(part is None for part in original):
                return []
            edition = [alias] + [part for part in original if part is not None]
        if not _valid_members(edition):
            log.warning(
                "card edition %s has invalid physical membership; edit refused", edition[0].id
            )
            return []
        editions.append(edition)
    return max(
        editions,
        key=lambda edition: (
            edition[0].kind is PublicationKind.MESSAGE_PART,
            (edition[0].id or 0)
            if edition[0].kind is PublicationKind.MESSAGE_PART
            else len(edition),
            -(edition[0].id or 0),
        ),
        default=[],
    )


def _physical_targets(edition: list[Publication]) -> tuple[int, ...]:
    targets: list[int] = []
    missing = False
    for part in edition:
        target = (
            part.message_id
            if part.status is PublicationStatus.SENT
            else part.delivery.edit_message_id
            if part.delivery is not None
            else None
        )
        if target is None:
            missing = True
        elif missing:
            return ()
        else:
            targets.append(target)
    return tuple(targets)


def _active_edit(
    repo: BillRepository, channel_id: str, aliases: list[Publication], targets: tuple[int, ...]
) -> bool:
    owners = {alias.id for alias in aliases}
    for pending in repo.list_open_deliveries(channel_id):
        plan = pending.delivery
        if pending.kind is not PublicationKind.MESSAGE_PART or plan is None:
            continue
        root = repo.publication_by_id(plan.continuation_root) if plan.continuation_root else pending
        owner = card_revision_owner(repo, root) if root is not None else None
        if owner is not None and owner.id in owners:
            return True
        if plan.edit_message_id in targets or (
            root is not None
            and root.delivery is not None
            and root.delivery.edit_message_id in targets
        ):
            return True
    return False


def edit_card(
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
) -> CardEditResult:
    """Freeze every part before API; advance the card only after the final receipt."""
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
        return CardEditResult()
    if open_card_edit_ids(repo, owner):
        return CardEditResult()
    assert owner.id is not None, "a stored delivered owner has its publication identity"
    aliases = repo.card_aliases(channel_id, owner.message_id)
    current = _physical_edition(repo, aliases)
    if not current:
        return CardEditResult()
    targets = _physical_targets(current)
    if (
        not targets
        or targets[0] != owner.message_id
        or len(set(targets)) != len(targets)
        or _active_edit(repo, channel_id, aliases, targets)
    ):
        return CardEditResult()
    today = today or clock.now().astimezone(local_tz).date()
    desired = digest if digest is not None else publisher.card_digest(bill, today=today)
    if desired == owner.rendered_sha256:
        return CardEditResult()
    previous = current[0]
    if previous.rendered_sha256 == desired and any(
        part.status is PublicationStatus.DISMISSED for part in current
    ):
        return CardEditResult()
    if load_print is not None:
        print_info = load_print()
    texts = publisher.render_card_parts(bill, print_info, minimum=len(targets), today=today)
    ref = f"card/{owner.id}/{desired}-{previous.id}"
    if (
        repo.get_publication(
            bill.term, bill.number, PublicationKind.MESSAGE_PART, channel_id, ref=f"{ref}/1"
        )
        is not None
    ):
        return CardEditResult()
    snapshot = continuation_snapshot(bill)
    identities: list[int] = []
    with repo.atomic():
        for index, text in enumerate(texts):
            identity = repo.create_publication(
                Publication(
                    term=bill.term,
                    number=bill.number,
                    channel_id=channel_id,
                    kind=PublicationKind.MESSAGE_PART,
                    status=PublicationStatus.QUEUED,
                    created_at=clock.now(),
                    ref=f"{ref}/{index + 1}",
                    delivery=DeliveryPlan(
                        bill_json=snapshot,
                        rendered_text=text,
                        edit_message_id=targets[index] if index < len(targets) else None,
                        revision_of=owner.id if not identities else None,
                        today=today,
                        reply_to=owner.message_id if not identities else None,
                        continuation_parent=identities[-1] if identities else owner.id,
                        continuation_root=identities[0] if identities else None,
                        continuation_index=index + 1,
                    ),
                )
            )
            identities.append(identity)
        root = repo.publication_by_id(identities[0])
        assert root is not None and root.delivery is not None, "the physical edition was saved"
        plan = root.delivery.model_copy(update={"continuation_ids": tuple(identities[1:])})
        if not repo.replace_unattempted_delivery(identities[0], plan):
            raise ValueError("Cannot change an attempted card edition")
        repo.set_card_digest(identities[0], desired)
        repo.mark_publication(identities[0], PublicationStatus.PENDING, count_attempt=False)
        root = repo.publication_by_id(identities[0])
        assert root is not None, "the immutable root is saved before its edit"
    attempted = delivery_attempts if delivery_attempts is not None else set[int]()
    attempted.add(identities[0])
    sent = send_publication(
        repo,
        clock,
        identities[0],
        partial(publisher.publish_prepared, root),
        checkpoint=checkpoint,
    )
    if sent is None:
        return CardEditResult()
    parts = send_continuations(repo, clock, publisher, root, attempted, checkpoint=checkpoint)
    latest = repo.publication_by_id(owner.id)
    return CardEditResult(
        completed=latest is not None and latest.rendered_sha256 == desired,
        new_messages=parts.sent,
        errors=tuple(parts.errors),
    )

import logging
from collections.abc import Callable, Sequence
from datetime import datetime

from lexinform.errors import DeliveryUncertainError, ServiceUnavailableError, StateCheckpointError
from lexinform.models import Publication, PublicationKind, PublicationStatus
from lexinform.ports import BillRepository, Clock, PublishResult, StateCheckpoint

log = logging.getLogger(__name__)


def send_publication(
    repo: BillRepository,
    clock: Clock,
    pub_id: int,
    send: Callable[[], PublishResult | int],
    *,
    checkpoint: StateCheckpoint | None = None,
) -> int | None:
    return send_publications(repo, clock, (pub_id,), send, checkpoint=checkpoint)


def send_publications(
    repo: BillRepository,
    clock: Clock,
    pub_ids: Sequence[int],
    send: Callable[[], PublishResult | int],
    *,
    checkpoint: StateCheckpoint | None = None,
) -> int | None:
    """The caller persists pending work before sending; only definite failures spend attempts."""
    if checkpoint is not None:
        checkpoint()
    try:
        sent = send()
    except StateCheckpointError:
        raise
    except DeliveryUncertainError as exc:
        with repo.atomic():
            for pub_id in pub_ids:
                repo.mark_publication(
                    pub_id, PublicationStatus.UNKNOWN, error=exc.describe(), count_attempt=False
                )
        if checkpoint is not None:
            checkpoint()
        raise
    except ServiceUnavailableError as exc:
        _fail_deliveries(repo, pub_ids, exc.describe(), count_attempt=False)
        if checkpoint is not None:
            checkpoint()
        raise
    except Exception as exc:
        log.exception("publications %s failed: %s", pub_ids, exc)
        _fail_deliveries(repo, pub_ids, f"{type(exc).__name__}: {exc}", count_attempt=True)
        if checkpoint is not None:
            checkpoint()
        return None
    message_id = sent if isinstance(sent, int) else sent.message_id
    documents = None if isinstance(sent, int) else list(sent.document_message_ids)
    with repo.atomic():
        for pub_id in pub_ids:
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
    if checkpoint is not None:
        checkpoint()
    return message_id


def _fail_deliveries(
    repo: BillRepository, pub_ids: Sequence[int], error: str, *, count_attempt: bool
) -> None:
    with repo.atomic():
        for pub_id in pub_ids:
            repo.mark_publication(
                pub_id, PublicationStatus.FAILED, error=error, count_attempt=count_attempt
            )


def card_revision_owner(repo: BillRepository, publication: Publication) -> Publication | None:
    plan = publication.delivery
    if (
        publication.kind is not PublicationKind.MESSAGE_PART
        or plan is None
        or plan.continuation_root is not None
        or plan.edit_message_id is None
    ):
        return None
    owner_id = plan.revision_of
    if owner_id is None:
        pieces = (publication.ref or "").split("/")
        if len(pieces) != 4 or pieces[0] != "card" or pieces[-1] != "1":
            return None
        try:
            owner_id = int(pieces[1])
        except ValueError:
            return None
    owner = repo.publication_by_id(owner_id)
    if (
        owner is None
        or owner.kind is not PublicationKind.NEW_BILL
        or owner.status is not PublicationStatus.SENT
        or owner.channel_id != publication.channel_id
        or owner.term != publication.term
        or owner.number != publication.number
        or owner.message_id != plan.edit_message_id
    ):
        return None
    return owner


def complete_delivery(
    repo: BillRepository, publication: Publication, *, message_id: int, sent_at: datetime
) -> None:
    """Apply the saved message's effects inside the send or operator-confirmation transaction."""
    plan = publication.delivery
    if plan is None:
        return
    if plan.continuation_ids and any(
        part is None or part.status is not PublicationStatus.SENT
        for identity in plan.continuation_ids
        for part in (repo.publication_by_id(identity),)
    ):
        return
    owner = card_revision_owner(repo, publication)
    if (
        owner is not None
        and plan.revision_of is not None
        and publication.status is PublicationStatus.SENT
        and publication.rendered_sha256
    ):
        assert owner.id is not None, "a revision targets a stored sent card"
        repo.set_card_digest(owner.id, publication.rendered_sha256)
    repo.release_planned_changes(
        plan.held_change_ids, publication.channel_id, message_id=message_id, sent_at=sent_at
    )
    if plan.digest_event_ids:
        assert publication.id is not None
        for source in repo.complete_digest_events(
            publication.id, message_id=message_id, sent_at=sent_at
        ):
            complete_delivery(repo, source, message_id=message_id, sent_at=sent_at)
    if plan.continuation_root is not None:
        root = repo.publication_by_id(plan.continuation_root)
        if (
            root is not None
            and root.status is PublicationStatus.SENT
            and root.message_id is not None
        ):
            complete_delivery(repo, root, message_id=root.message_id, sent_at=sent_at)
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

from datetime import datetime
from typing import Literal, Never

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.content_sources import SOURCE_CONTEXT_INVALID
from lexinform.models import (
    DeliveryPlan,
    DeliveryResolution,
    IncomingCommand,
    OutcomeStatus,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
    RunMode,
    StatusChange,
    UpdateSnapshot,
)
from lexinform.services.message_parts import freeze_parts
from lexinform.services.posting import Poster
from lexinform.services.publications import send_publications, source_context_blocked
from lexinform.services.tracking_result import TrackingResult
from tests.fakes import FakePublisher, FakeSejmGateway, FixedClock
from tests.harness import CHANNEL, World


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


def forbidden() -> Never:
    raise AssertionError("a reserved block cannot reach a checkpoint or publisher callback")


@pytest.mark.parametrize("rendered", [False, True])
def test_poster_checks_persisted_block_before_freeze_or_attempt(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime, rendered: bool
) -> None:
    publication = queued(repo, process_3039, now, rendered=rendered)
    stale = publication.model_copy(update={"error": None})
    publisher = FakePublisher()
    poster = Poster(
        repo,
        publisher,
        FixedClock(now),
        gateway=FakeSejmGateway(),
        channel_id="@test",
        max_attempts=3,
        checkpoint=forbidden,
    )
    before = repo.dump()
    assert not poster.ready(stale) and poster.delivery_paused(stale)
    assert not poster.deliver(stale)
    poster.queue_update(stale)
    poster.flush_updates(TrackingResult())
    assert not publisher.sent and not publisher.edited and not publisher.update_roundups
    assert repo.dump() == before
    assert publication.error == SOURCE_CONTEXT_INVALID and publication.attempts == 0


def test_common_group_send_checks_members_before_checkpoint_and_callback(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    ordinary = queued(repo, process_3039, now, error="ordinary", ref="ordinary")
    blocked = queued(repo, process_3039, now)
    assert ordinary.id is not None and blocked.id is not None, "queued assigns stored IDs"
    before = repo.dump()
    assert (
        send_publications(
            repo, FixedClock(now), (ordinary.id, blocked.id), forbidden, checkpoint=forbidden
        )
        is None
    )
    assert repo.dump() == before


def test_direct_publisher_and_freeze_reject_reserved_block_without_io_or_writes(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    publication = queued(repo, process_3039, now, rendered=False)
    publisher = FakePublisher()
    before = repo.dump()
    with pytest.raises(ValueError, match="source context"):
        publisher.publish_prepared(publication)
    with pytest.raises(ValueError, match="source context"):
        freeze_parts(
            repo,
            FixedClock(now),
            publisher,
            publication.model_copy(update={"error": None}),
            "x" * 10_000,
        )
    assert not publisher.sent and not publisher.edited
    assert repo.dump() == before


def test_public_group_retry_checks_a_newly_blocked_persisted_member(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    members = [queued(repo, process_3039, now, error=None, ref=ref) for ref in ("first", "second")]
    snapshots: list[UpdateSnapshot] = []
    for member in members:
        assert member.id is not None and member.delivery is not None, "queued persists each member"
        assert member.delivery.change_json is not None, "queued freezes the original change"
        snapshots.append(
            UpdateSnapshot(
                publication_id=member.id,
                bill_json=member.delivery.bill_json,
                change_json=member.delivery.change_json,
                card_message_id=None,
            )
        )
    for member in members:
        assert member.id is not None and member.delivery is not None, "queued persists each member"
        assert repo.replace_unattempted_delivery(
            member.id,
            member.delivery.model_copy(
                update={"update_group": tuple(snapshots), "group_text": "Frozen group"}
            ),
        )
    root = repo.publication_by_id(snapshots[0].publication_id)
    assert root is not None, "the public repository retains the saved group root"
    repo.mark_publication(
        snapshots[1].publication_id,
        PublicationStatus.QUEUED,
        error=SOURCE_CONTEXT_INVALID,
        count_attempt=False,
    )
    publisher = FakePublisher()
    poster = Poster(
        repo,
        publisher,
        FixedClock(now),
        gateway=FakeSejmGateway(),
        channel_id="@test",
        max_attempts=3,
        checkpoint=forbidden,
    )
    before = repo.dump()
    assert not poster.retry_update_group(root, TrackingResult())
    assert not publisher.update_roundups and not publisher.sent
    assert repo.dump() == before


def test_common_send_follows_a_continuation_to_its_blocked_root(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    root = queued(repo, process_3039, now)
    assert root.id is not None, "queued stores the root before its continuation"
    assert root.delivery is not None, "queued retains the root's frozen bill"
    child = root.model_copy(
        update={
            "id": None,
            "kind": PublicationKind.MESSAGE_PART,
            "ref": f"message/{root.id}/2",
            "error": None,
            "delivery": DeliveryPlan(
                bill_json=root.delivery.bill_json,
                rendered_text="Frozen continuation",
                continuation_root=root.id,
                continuation_parent=root.id,
                continuation_index=2,
            ),
        }
    )
    identity = repo.create_publication(child)
    saved = repo.publication_by_id(identity)
    assert saved is not None, "the public repository stores the physical continuation"
    assert source_context_blocked(repo, saved)
    before = repo.dump()
    assert send_publications(repo, FixedClock(now), (identity,), forbidden) is None
    assert repo.dump() == before


@pytest.mark.parametrize("relation", ["continuation_ids", "sitting_group"])
def test_public_root_send_checks_blocked_retained_children_before_any_effect(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
    now: datetime,
    relation: Literal["continuation_ids", "sitting_group"],
) -> None:
    root = queued(repo, process_3039, now, error=None, ref="root")
    child = queued(repo, process_3039, now, error=None, ref="child")
    assert root.id is not None and root.delivery is not None, "queued stores the root plan"
    assert child.id is not None, "queued stores the child's identity before linkage"
    assert repo.replace_unattempted_delivery(
        root.id, root.delivery.model_copy(update={relation: (child.id,)})
    )
    repo.mark_publication(
        child.id, PublicationStatus.QUEUED, error=SOURCE_CONTEXT_INVALID, count_attempt=False
    )
    before = repo.dump()
    assert (
        send_publications(repo, FixedClock(now), (root.id,), forbidden, checkpoint=forbidden)
        is None
    )
    assert repo.dump() == before


@pytest.mark.parametrize("error", [None, "ordinary", "source_context_invalid "])
def test_other_errors_keep_the_existing_send_behavior(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime, error: str | None
) -> None:
    publication = queued(repo, process_3039, now, error=error)
    assert publication.id is not None, "queued stores the ordinary delivery"
    assert send_publications(repo, FixedClock(now), (publication.id,), lambda: 777) == 777
    saved = repo.publication_by_id(publication.id)
    assert saved is not None and saved.status is PublicationStatus.SENT


def test_operator_retry_cannot_resolve_or_clear_a_reserved_block(
    process_3039: ProcessDetail,
) -> None:
    current = World()
    publication = queued(current.repo, process_3039, current.clock.now(), channel=CHANNEL)
    assert publication.id is not None, "queued stores the command's exact target"
    current.command(f"/delivery {publication.id} retry")
    current.run(mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0)
    outcome = current.replier.replies[-1][1]
    assert outcome.status is OutcomeStatus.ERROR and "source context" in outcome.note
    assert current.repo.publication_by_id(publication.id) == publication
    assert not current.repo.delivery_resolutions(publication.id)
    assert not current.publisher.sent and not current.publisher.edited


def test_repository_resolution_cannot_retry_unknown_child_under_a_blocked_root(
    repo: SqliteBillRepository, process_3039: ProcessDetail, now: datetime
) -> None:
    root = queued(repo, process_3039, now)
    assert root.id is not None and root.delivery is not None, "queued stores the blocked root"
    child = root.model_copy(
        update={
            "id": None,
            "kind": PublicationKind.MESSAGE_PART,
            "ref": f"message/{root.id}/2",
            "status": PublicationStatus.UNKNOWN,
            "error": None,
            "delivery": DeliveryPlan(
                bill_json=root.delivery.bill_json,
                rendered_text="Frozen child",
                continuation_root=root.id,
                continuation_parent=root.id,
                continuation_index=2,
            ),
        }
    )
    identity = repo.create_publication(child)
    command = IncomingCommand(
        update_id=77777,
        chat_id="test",
        message_id=77,
        text=f"/delivery {identity} retry",
        received_at=now,
    )
    repo.record_command(command)
    before = repo.dump()
    assert not repo.resolve_delivery(
        DeliveryResolution(
            publication_id=identity,
            term=child.term,
            number=child.number,
            kind=child.kind,
            channel_id=child.channel_id,
            action="retry",
            command=command,
            resolved_at=now,
        )
    )
    assert not repo.delivery_resolutions(identity) and repo.dump() == before

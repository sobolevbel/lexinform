import datetime as dt
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Never

import pytest

from lexinform.errors import DeliveryUncertainError, StateCheckpointError
from lexinform.models import (
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
    ReviewContext,
    ReviewWork,
    Stage,
    StatusChange,
)
from lexinform.ports import PublishResult
from lexinform.services.analysis import AnalysisService
from lexinform.services.message_parts import freeze_parts
from lexinform.services.tracking_result import TrackingResult
from tests.checkpoint_state import StoredState, saved_state
from tests.harness import CHANNEL, TERM, World
from tests.scenario.test_card_remote_checkpoints import (
    Mode,
    RunnerLost,
    connect,
    git,
    reject_checkpoint,
)
from tests.scenario.test_commands import _commands_only
from tests.scenario.test_group_remote_checkpoints import restored

KINDS = (
    PublicationKind.CONSULTATION_REVIEW,
    PublicationKind.COMMISSION_REVIEW,
    PublicationKind.AMENDMENTS_REVISION,
)


@dataclass(frozen=True)
class ReviewCase:
    world: World
    identity: int
    original: Publication | None
    held: int


def row(world: StoredState, identity: int) -> Publication:
    publication = world.repo.publication_by_id(identity)
    assert publication is not None, "the fixture stores each physical message before delivery"
    return publication


def plan(world: StoredState, identity: int) -> DeliveryPlan:
    delivery = row(world, identity).delivery
    assert delivery is not None, "each physical message retains its immutable delivery plan"
    return delivery


def queued(
    kind: PublicationKind,
    mode: Mode = "on",
    *,
    long: bool = False,
    enabled: bool = True,
) -> ReviewCase:
    world = World(
        profile="production",
        reader_attention=mode,
        consultation_reviews_enabled=enabled,
        commission_reviews_enabled=enabled,
        batch_kinds=frozenset({"analysis", "consultation_review", "commission_review"}),
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    world.batch.resolve()
    world.run()
    poster = world.container.poster(dry_run=False)
    change = poster.record_change(
        StatusChange(
            term=TERM,
            number="3039",
            old_fingerprint="old-review",
            new_fingerprint="held-review",
            new_stages=[Stage(stage_type="CommitteeWork", stage_name="Prace w komisji")],
            detected_at=world.clock.now(),
        )
    )
    assert change is not None and change.id is not None, "the fixture records a real held change"
    poster.hold(world.bill("3039"), change)
    original = None
    if kind is PublicationKind.AMENDMENTS_REVISION:
        original_change = poster.record_change(
            change.model_copy(update={"id": None, "new_fingerprint": "original-amendments"})
        )
        assert original_change is not None and original_change.id is not None, (
            "Poster.record_change assigns the stored original event identity"
        )
        original = Publication(
            term=TERM,
            number="3039",
            kind=PublicationKind.STATUS_UPDATE,
            channel_id=CHANNEL,
            status=PublicationStatus.SENT,
            message_id=777,
            created_at=world.clock.now(),
            sent_at=world.clock.now(),
            ref="original-amendments",
            status_change_id=original_change.id,
            delivery=DeliveryPlan(
                bill_json=world.bill("3039").model_dump_json(),
                change_json=change.model_dump_json(),
                rendered_text="<b>Исходное сообщение о поправках</b>",
                reply_to=world.card_id("3039"),
                today=world.clock.now().date(),
            ),
        )
        original.id = world.repo.create_publication(original)
    publication = Publication(
        term=TERM,
        number="3039",
        kind=kind,
        channel_id=CHANNEL,
        status=PublicationStatus.QUEUED,
        ref="saved-review",
        created_at=world.clock.now(),
        delivery=DeliveryPlan(
            bill_json=world.bill("3039").model_dump_json(),
            change_json=change.model_dump_json(),
            reply_to=world.card_id("3039"),
            today=world.clock.now().date(),
            policy_version="attention-v1",
            edit_message_id=original.message_id if original else None,
            revision_of=original.id if original else None,
            held_change_ids=(change.id,),
        ),
    )
    publication.id = world.repo.create_publication(publication)
    text = "<b>Сохранённое сообщение: сбор 200 PLN, заявление не подавать заново.</b>"
    if long:
        text += "\nУсловие для старых заявлений, срок 30 дней. " * 220
    freeze_parts(world.repo, world.clock, world.publisher, publication, text)
    world.clock.advance(days=30)
    world.repo.upsert_summary(
        world.bill("3039").summary.model_copy(update={"title": "Changed live title"}),
        now=world.clock.now(),
    )
    return ReviewCase(world, publication.id, original, change.id)


def enable(world: World) -> None:
    world.container = replace(
        world.container,
        settings=world.container.settings.model_copy(
            update={"consultation_reviews_enabled": True, "commission_reviews_enabled": True}
        ),
    )
    world.pipeline = world.container.pipeline(dry_run=False)


def refuse_render(*args: object, **kwargs: object) -> Never:
    raise AssertionError("saved review HTML must never be rendered from current data")


def held_status(world: StoredState, identity: int) -> PublicationStatus:
    publication = world.repo.get_update_publication(identity, CHANNEL)
    assert publication is not None, "Poster.hold creates the source publication"
    return publication.status


@pytest.mark.parametrize(
    "mode,kind",
    [pytest.param("on", kind, id=f"{kind}-on") for kind in KINDS]
    + [
        pytest.param("off", KINDS[0], id=f"{KINDS[0]}-off"),
        pytest.param("shadow", KINDS[1], id=f"{KINDS[1]}-shadow"),
    ],
)
def test_saved_review_parts_checkpoint_exact_html_parent_and_edit_target_before_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, kind: PublicationKind
) -> None:
    case = queued(kind, mode, long=True)
    world = case.world
    saved = plan(world, case.identity)
    assert len(saved.continuation_ids) >= 2, "the fixture exercises root and final part boundaries"
    identities = (case.identity, *saved.continuation_ids)
    payloads = {identity: plan(world, identity).rendered_text for identity in identities}
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared
    observed: list[int] = []

    def inspect(publication: Publication) -> PublishResult:
        assert publication.id is not None, "prepared review sends retain their saved identity"
        with saved_state(git(remote, "show", "state:state.sql")) as durable:
            frozen = row(durable, publication.id)
            assert (
                frozen.status is PublicationStatus.PENDING
                and frozen.delivery == publication.delivery
            )
            assert frozen.delivery is not None, "the remote pending row has its immutable payload"
            assert frozen.delivery.rendered_text == payloads[publication.id]
            assert held_status(durable, case.held) is PublicationStatus.SKIPPED
            if publication.id == case.identity:
                assert frozen.delivery.reply_to == saved.reply_to
                assert frozen.delivery.edit_message_id == saved.edit_message_id
                for identity in saved.continuation_ids:
                    assert row(durable, identity).status is PublicationStatus.QUEUED
            else:
                parent = row(durable, frozen.delivery.continuation_parent or 0)
                assert parent.status is PublicationStatus.SENT
                assert frozen.delivery.reply_to == parent.message_id
            if case.original is not None:
                assert case.original.id is not None, "the revision preserves the original event row"
                assert row(durable, case.original.id) == case.original
            observed.append(publication.id)
            return original(publication)

    monkeypatch.setattr(world.publisher, "render_delivery", refuse_render)
    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    assert world.container.poster(dry_run=False).deliver(row(world, case.identity))

    assert observed == list(identities)
    fresh = restored(remote, world, tmp_path)
    assert all(row(fresh, identity).status is PublicationStatus.SENT for identity in identities)
    assert held_status(fresh, case.held) is PublicationStatus.SENT
    if case.original is not None:
        assert case.original.id is not None, "the fixture stores the original amendment event"
        assert row(fresh, case.original.id) == case.original
        assert world.publisher.edited[-1][1] == case.original.message_id
        assert world.publisher.edited[-1][0].text == saved.rendered_text
    assert not world.llm.contexts and not world.repo.list_queued_batch_intents()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("at", [1, 2, 5, 6])
def test_review_root_or_final_checkpoint_failure_stops_later_effects_and_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: PublicationKind, at: int
) -> None:
    case = queued(kind, long=True)
    world = case.world
    remote = connect(world, tmp_path)
    identities = (case.identity, *plan(world, case.identity).continuation_ids)
    assert len(identities) == 3, "the checkpoint matrix covers all three physical review parts"
    reject_checkpoint(monkeypatch, at)
    sends = len(world.publisher.sent) + len(world.publisher.edited)

    with pytest.raises(StateCheckpointError):
        world.container.delivery_service(dry_run=False).run()

    assert len(world.publisher.sent) + len(world.publisher.edited) - sends == at // 2
    assert held_status(world, case.held) is (
        PublicationStatus.SENT if at == 6 else PublicationStatus.SKIPPED
    )
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    assert held_status(fresh, case.held) is PublicationStatus.SKIPPED
    if at % 2 == 0:
        assert row(fresh, identities[at // 2 - 1]).status is PublicationStatus.UNKNOWN
        enable(fresh)
        fresh.container.delivery_service(dry_run=False).run()
        assert not fresh.publisher.sent and not fresh.publisher.edited
    if case.original is not None:
        assert case.original.id is not None, "the fixture stores the original amendment event"
        assert row(fresh, case.original.id) == case.original


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("accepted", [False, True])
def test_review_unknown_requires_audited_retry_of_exact_payload_after_fresh_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: PublicationKind, accepted: bool
) -> None:
    case = queued(kind)
    world = case.world
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared

    def lost(publication: Publication) -> PublishResult:
        if accepted:
            original(publication)
            raise DeliveryUncertainError("review accepted but response lost")
        raise RunnerLost()

    monkeypatch.setattr(world.publisher, "publish_prepared", lost)
    with pytest.raises(DeliveryUncertainError if accepted else RunnerLost):
        world.container.poster(dry_run=False).deliver(row(world, case.identity))
    fresh = restored(remote, world, tmp_path)
    enable(fresh)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    saved = plan(fresh, case.identity)
    fresh.container.delivery_service(dry_run=False).run()
    assert row(fresh, case.identity).status is PublicationStatus.UNKNOWN
    assert not fresh.publisher.sent and not fresh.publisher.edited
    incoming = fresh.command(f"/delivery {case.identity} retry")
    observed: list[int] = []
    retried = fresh.publisher.publish_prepared

    def retry(publication: Publication) -> PublishResult:
        with saved_state(git(remote, "show", "state:state.sql")) as durable:
            history = durable.repo.delivery_resolutions(case.identity)
            assert len(history) == 1 and history[0].command == incoming
            assert plan(durable, case.identity) == saved
            assert row(durable, case.identity).status is PublicationStatus.PENDING
            observed.append(case.identity)
            return retried(publication)

    monkeypatch.setattr(fresh.publisher, "render_delivery", refuse_render)
    monkeypatch.setattr(fresh.publisher, "publish_prepared", retry)
    _commands_only(fresh)
    fresh.container.delivery_service(dry_run=False).run()

    assert observed == [case.identity]
    assert row(fresh, case.identity).status is PublicationStatus.SENT
    assert held_status(fresh, case.held) is PublicationStatus.SENT
    if case.original is not None:
        assert case.original.id is not None, "the fixture stores the original amendment event"
        assert row(fresh, case.original.id) == case.original


@pytest.mark.parametrize("kind", KINDS)
def test_review_unknown_confirmation_preserves_target_and_never_sends(
    tmp_path: Path, kind: PublicationKind
) -> None:
    case = queued(kind)
    world = case.world
    world.repo.mark_publication(case.identity, PublicationStatus.UNKNOWN, count_attempt=False)
    saved = plan(world, case.identity)
    remote = connect(world, tmp_path)
    target = saved.edit_message_id or 888
    before = len(world.publisher.sent) + len(world.publisher.edited)
    if saved.edit_message_id is not None:
        world.command(f"/delivery {case.identity} confirm {target + 1}")
        _commands_only(world)
        assert row(world, case.identity).status is PublicationStatus.UNKNOWN
        assert not world.repo.delivery_resolutions(case.identity)
    world.command(f"/delivery {case.identity} confirm {target}")

    _commands_only(world)
    world.container.state_checkpoint()()

    fresh = restored(remote, world, tmp_path)
    assert row(fresh, case.identity).status is PublicationStatus.SENT
    assert row(fresh, case.identity).message_id == target
    assert plan(fresh, case.identity) == saved
    assert held_status(fresh, case.held) is PublicationStatus.SENT
    assert len(world.publisher.sent) + len(world.publisher.edited) == before
    assert len(fresh.repo.delivery_resolutions(case.identity)) == 1


@pytest.mark.parametrize("kind", KINDS[:2])
@pytest.mark.parametrize(
    "status", [PublicationStatus.QUEUED, PublicationStatus.FAILED, PublicationStatus.UNKNOWN]
)
@pytest.mark.parametrize("child", [False, True])
def test_default_paused_reviews_do_not_send_checkpoint_or_change_any_payload(
    tmp_path: Path, kind: PublicationKind, status: PublicationStatus, child: bool
) -> None:
    case = queued(kind, long=child, enabled=False)
    world = case.world
    identity = plan(world, case.identity).continuation_ids[0] if child else case.identity
    if child:
        world.repo.mark_publication(
            case.identity, PublicationStatus.SENT, message_id=800, count_attempt=False
        )
    world.repo.mark_publication(identity, status, count_attempt=status is PublicationStatus.FAILED)
    saved = world.repo.dump()
    remote = connect(world, tmp_path)
    baseline = git(remote, "rev-parse", "state")
    before = len(world.publisher.sent) + len(world.publisher.edited)

    assert not world.container.poster(dry_run=False).deliver(row(world, identity))
    result = TrackingResult()
    world.container.document_reviews(dry_run=False).advance(result, publish=True)

    assert world.repo.dump() == saved
    assert git(remote, "rev-parse", "state") == baseline
    assert len(world.publisher.sent) + len(world.publisher.edited) == before
    assert not result.failed and not result.partial_errors
    assert not world.repo.list_queued_batch_intents()


@pytest.mark.parametrize("kind", [PublicationKind.ACT_APPLICATION, PublicationKind.EFFECTIVE_RULES])
@pytest.mark.parametrize("child", [False, True])
def test_unsupported_final_act_reviews_remain_skipped_without_send_or_checkpoint(
    tmp_path: Path, kind: PublicationKind, child: bool
) -> None:
    case = queued(kind, long=child)
    world = case.world
    identity = plan(world, case.identity).continuation_ids[0] if child else case.identity
    if child:
        world.repo.mark_publication(
            case.identity, PublicationStatus.SENT, message_id=800, count_attempt=False
        )
    saved = plan(world, identity)
    remote = connect(world, tmp_path)
    baseline = git(remote, "rev-parse", "state")
    before = len(world.publisher.sent) + len(world.publisher.edited)

    assert not world.container.poster(dry_run=False).deliver(row(world, identity))

    assert row(world, identity).status is PublicationStatus.SKIPPED
    assert plan(world, identity) == saved
    assert git(remote, "rev-parse", "state") == baseline
    assert len(world.publisher.sent) + len(world.publisher.edited) == before


@pytest.mark.parametrize("fatal", [False, True])
def test_review_work_boundary_propagates_only_fatal_state_errors(
    monkeypatch: pytest.MonkeyPatch, fatal: bool
) -> None:
    case = queued(PublicationKind.CONSULTATION_REVIEW)
    world = case.world
    context = ReviewContext(
        purpose="consultation_review",
        number="3039",
        title="Saved review",
        documents=(),
    )
    work = ReviewWork(
        memo_key="saved-review-work",
        purpose="consultation_review",
        context=context,
        since=world.clock.now(),
        baseline_day=dt.date(2026, 9, 9),
    )
    world.repo.save_review_work(TERM, "3039", (work,))

    def failed(*args: object, **kwargs: object) -> Never:
        if fatal:
            raise StateCheckpointError("review persistence failed")
        raise ValueError("invalid review result")

    monkeypatch.setattr(AnalysisService, "request_document_review", failed)
    before = len(world.publisher.sent) + len(world.publisher.edited)
    result = TrackingResult()
    if fatal:
        with pytest.raises(StateCheckpointError, match="review persistence failed"):
            world.container.document_reviews(dry_run=False).advance(result, publish=True)
        assert row(world, case.identity).status is PublicationStatus.QUEUED
        assert len(world.publisher.sent) + len(world.publisher.edited) == before
    else:
        world.container.document_reviews(dry_run=False).advance(result, publish=False)
        assert (
            len(result.partial_errors) == 1 and "invalid review result" in result.partial_errors[0]
        )
    assert world.bill("3039").review_work == (work,)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("attempts", [0, 1])
def test_failed_legacy_review_without_html_is_never_rewritten_or_checkpointed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: PublicationKind, attempts: int
) -> None:
    case = queued(kind)
    world = case.world
    legacy = plan(world, case.identity).model_copy(update={"rendered_text": ""})
    assert world.repo.replace_unattempted_delivery(case.identity, legacy)
    world.repo.mark_publication(
        case.identity, PublicationStatus.FAILED, count_attempt=bool(attempts)
    )
    saved = row(world, case.identity)
    remote = connect(world, tmp_path)
    baseline = git(remote, "rev-parse", "state")
    before = len(world.publisher.sent) + len(world.publisher.edited)
    monkeypatch.setattr(world.publisher, "render_delivery", lambda _: "Старый снимок")

    with pytest.raises(ValueError, match="Cannot change an attempted message payload"):
        world.container.poster(dry_run=False).deliver(saved)

    assert row(world, case.identity) == saved
    assert git(remote, "rev-parse", "state") == baseline
    assert len(world.publisher.sent) + len(world.publisher.edited) == before


@pytest.mark.parametrize("kind", KINDS[:2])
@pytest.mark.parametrize("at", [1, 2])
def test_review_advance_checkpoint_failure_stops_the_next_reader_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: PublicationKind, at: int
) -> None:
    case = queued(kind)
    world = case.world
    next_root = row(world, case.identity).model_copy(update={"id": None, "ref": "later-review"})
    next_id = world.repo.create_publication(next_root)
    connect(world, tmp_path)
    reject_checkpoint(monkeypatch, at)
    before = len(world.publisher.sent)

    with pytest.raises(StateCheckpointError):
        world.container.document_reviews(dry_run=False).advance(TrackingResult(), publish=True)

    assert len(world.publisher.sent) - before == int(at == 2)
    assert row(world, next_id).status is PublicationStatus.QUEUED
    assert row(world, next_id).attempts == 0


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("attempts", [0, 1])
def test_failed_saved_review_keeps_html_and_edit_target_while_checkpointing_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: PublicationKind, attempts: int
) -> None:
    case = queued(kind)
    world = case.world
    saved = plan(world, case.identity)
    world.repo.mark_publication(
        case.identity, PublicationStatus.FAILED, count_attempt=bool(attempts)
    )
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared

    def inspect(publication: Publication) -> PublishResult:
        with saved_state(git(remote, "show", "state:state.sql")) as durable:
            assert row(durable, case.identity).status is PublicationStatus.PENDING
            assert row(durable, case.identity).attempts == attempts
            assert plan(durable, case.identity) == saved == publication.delivery
            return original(publication)

    monkeypatch.setattr(world.publisher, "render_delivery", refuse_render)
    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    assert world.container.poster(dry_run=False).deliver(row(world, case.identity))

    with saved_state(git(remote, "show", "state:state.sql")) as durable:
        assert row(durable, case.identity).status is PublicationStatus.SENT
        assert plan(durable, case.identity) == saved

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

from lexinform.errors import DeliveryUncertainError, StateCheckpointError, TelegramUnavailableError
from lexinform.models import Bill, Publication, PublicationStatus, SejmTerm
from lexinform.ports import PublishResult
from lexinform.services.publishing import PublishingResult
from tests.harness import CHANNEL, TERM, World
from tests.scenario.test_card_edit_callers import owned, successor
from tests.scenario.test_card_remote_checkpoints import (
    Mode,
    connect,
    git,
    ready_world,
    reject_checkpoint,
    restored,
)
from tests.scenario.test_card_revision_reader import saved_edit
from tests.scenario.test_single_card_revision_producer import card, change

Shape = Literal["single_many", "many_same", "many_grow", "many_shrink"]


@dataclass(frozen=True)
class Edition:
    world: World
    baseline: Publication
    previous: tuple[Publication, ...]
    texts: tuple[str, ...]


def edition(world: World, number: str = "3039") -> list[Publication]:
    owner = owned(world, number)
    return world.repo.card_refresh_parts(owner.id or 0)


def prepare(mode: Mode = "on", shape: Shape = "single_many", *, second: bool = False) -> Edition:
    world = ready_world(mode, long=shape != "single_many", second=second)
    world.run()
    baseline = card(world)
    previous = tuple(edition(world))
    original = world.bill("3039").analysis
    assert original is not None, "the production fixture completed its analysis batch"
    if shape == "many_same":
        text = original.analysis.practical_impact.replace("200 PLN", "400 PLN")
    elif shape == "many_shrink":
        text = "Новая сумма 400 PLN; старые заявления повторно не подавать."
    else:
        text = "Новая сумма 400 PLN; старые заявления повторно не подавать. " * (
            600 if shape == "many_grow" else 220
        )
    change(world, text)
    texts = world.publisher.render_card_parts(
        world.bill("3039"), world.gateway.get_print(TERM, "3039"), minimum=len(previous)
    )
    assert (len(previous) == 1) is (shape == "single_many")
    assert (len(texts) > len(previous)) is (shape in ("single_many", "many_grow"))
    return Edition(world, baseline, previous, texts)


def refresh(world: World) -> None:
    world.container.tracking_service(dry_run=False).check_bill(world.bill("3039"))


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("shape", ["single_many", "many_same", "many_grow", "many_shrink"])
def test_every_multipart_edit_part_is_remote_before_api_and_final_receipt_alone_advances_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, shape: Shape
) -> None:
    case = prepare(mode, shape)
    world = case.world
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared
    observed: list[int] = []
    before_sent, before_edits = len(world.publisher.sent), len(world.publisher.edited)

    def inspect(saved: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        parts = edition(durable)
        assert tuple(part.delivery.rendered_text for part in parts if part.delivery) == case.texts
        assert all(part.delivery is not None for part in parts)
        index = len(observed)
        current = parts[index]
        assert saved.id == current.id and saved.delivery == current.delivery
        assert current.id is not None and current.delivery is not None, (
            "a physical operation and its payload precede the API"
        )
        assert current.status is PublicationStatus.PENDING
        assert current.channel_id == CHANNEL
        assert current.delivery.today == world.clock.now().date()
        assert Bill.model_validate_json(current.delivery.bill_json).analysis is None
        assert card(durable) == case.baseline
        root = parts[0]
        assert root.delivery is not None, "the root saves immutable edition membership"
        assert root.delivery.revision_of == case.baseline.id
        assert root.delivery.continuation_ids == tuple(part.id for part in parts[1:])
        if index:
            assert parts[index - 1].status is PublicationStatus.SENT
            assert current.delivery.reply_to == parts[index - 1].message_id
            assert current.delivery.continuation_root == root.id
        else:
            assert all(part.status is PublicationStatus.QUEUED for part in parts[1:])
        if index < len(case.previous):
            assert current.delivery.edit_message_id == case.previous[index].message_id
        else:
            assert current.delivery.edit_message_id is None
        observed.append(current.id)
        return original(saved)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    report = world.run(discover=False, analyze=False)

    added = len(case.texts) - len(case.previous)
    assert report.cards_refreshed == 1
    assert report.message_parts_sent == added
    assert len(observed) == len(case.texts)
    assert len(world.publisher.sent) == before_sent + added
    assert len(world.publisher.edited) == before_edits + len(case.previous)
    fresh = restored(remote, world, tmp_path)
    current = edition(fresh)
    assert all(part.status is PublicationStatus.SENT for part in current)
    assert card(fresh).rendered_sha256 == current[0].rendered_sha256
    assert [part.message_id for part in current[: len(case.previous)]] == [
        part.message_id for part in case.previous
    ]
    assert world.run(discover=False, analyze=False).cards_refreshed == 0


@pytest.mark.parametrize("shape", ["single_many", "many_shrink"])
@pytest.mark.parametrize("boundary", ["root_pre", "root_post", "last_pre", "last_post"])
def test_multipart_checkpoint_failures_restore_only_confirmed_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: Shape, boundary: str
) -> None:
    case = prepare(shape=shape)
    world = case.world
    remote = connect(world, tmp_path)
    total = len(case.texts)
    at = {"root_pre": 1, "root_post": 2, "last_pre": total * 2 - 1, "last_post": total * 2}[
        boundary
    ]
    before = len(world.publisher.edited) + len(world.publisher.sent)
    reject_checkpoint(monkeypatch, at)

    with pytest.raises(StateCheckpointError):
        refresh(world)

    assert len(world.publisher.edited) + len(world.publisher.sent) == before + at // 2
    assert (card(world).rendered_sha256 != case.baseline.rendered_sha256) is (
        boundary == "last_post"
    )
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    assert card(fresh) == case.baseline
    if boundary == "root_pre":
        assert tuple(edition(fresh)) == case.previous
        return
    current = edition(fresh)
    affected = current[0] if boundary.startswith("root") else current[-1]
    assert affected.status is (
        PublicationStatus.UNKNOWN if boundary.endswith("post") else PublicationStatus.QUEUED
    )
    if boundary.endswith("post"):
        before = len(world.publisher.edited) + len(world.publisher.sent)
        report = fresh.container.delivery_service(dry_run=False).run()
        assert report.cards_refreshed == 0
        assert len(world.publisher.edited) + len(world.publisher.sent) == before
        assert card(fresh) == case.baseline
    else:
        report = fresh.container.delivery_service(dry_run=False).run()
        assert report.cards_refreshed == 1
        assert card(fresh).rendered_sha256 == edition(fresh)[0].rendered_sha256


@pytest.mark.parametrize("shape", ["single_many", "many_shrink"])
@pytest.mark.parametrize("action", ["retry", "confirm"])
def test_unknown_final_part_requires_audited_resolution_and_retries_exact_frozen_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: Shape, action: str
) -> None:
    case = prepare(shape=shape)
    world = case.world
    remote = connect(world, tmp_path)
    reject_checkpoint(monkeypatch, len(case.texts) * 2)
    with pytest.raises(StateCheckpointError):
        refresh(world)
    local_last = edition(world)[-1]
    assert local_last.message_id is not None, "the physical API returned before checkpoint failure"
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    saved = edition(fresh)[-1]
    assert saved.id is not None and saved.delivery is not None, "the unknown part remains frozen"
    assert saved.status is PublicationStatus.UNKNOWN
    before = len(world.publisher.edited) + len(world.publisher.sent)
    assert fresh.container.delivery_service(dry_run=False).run().cards_refreshed == 0
    assert len(world.publisher.edited) + len(world.publisher.sent) == before
    incoming = fresh.command(
        f"/delivery {saved.id} "
        + ("retry" if action == "retry" else f"confirm {local_last.message_id}")
    )
    service = fresh.container.command_service(dry_run=False)
    assert service is not None, "the production fixture enables the audited command inbox"
    answer = service.handle_pending(min_score=3, run_started_at=fresh.clock.now())
    assert answer.failed == 0
    original = world.publisher.publish_prepared
    seen: list[int] = []

    def inspect(part: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        history = durable.repo.delivery_resolutions(saved.id or 0)
        assert len(history) == 1 and history[0].command == incoming
        assert part.id == saved.id and part.delivery == saved.delivery
        seen.append(part.id or 0)
        return original(part)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    report = fresh.container.delivery_service(dry_run=False).run()

    assert report.cards_refreshed == int(action == "retry")
    assert seen == ([saved.id] if action == "retry" else [])
    assert card(fresh).rendered_sha256 == edition(fresh)[0].rendered_sha256
    assert all(part.status is PublicationStatus.SENT for part in edition(fresh))
    assert fresh.container.delivery_service(dry_run=False).run().cards_refreshed == 0


def test_retention_protects_uncertain_edition_then_preserves_targets_for_next_growth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepare(shape="many_shrink")
    world = case.world
    remote = connect(world, tmp_path)
    reject_checkpoint(monkeypatch, len(case.texts) * 2)
    with pytest.raises(StateCheckpointError):
        refresh(world)
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    original = edition(fresh)
    fresh.repo.prune_history(
        before=fresh.clock.now() + dt.timedelta(days=1), memo_before=fresh.clock.now()
    )
    fresh.repo.restore(fresh.repo.dump())
    assert edition(fresh) == original
    assert all(part.delivery is not None for part in edition(fresh))
    fresh.command(f"/delivery {original[-1].id} retry")
    commands = fresh.container.command_service(dry_run=False)
    assert commands is not None, "the production fixture enables the operator command inbox"
    commands.handle_pending(min_score=3, run_started_at=fresh.clock.now())
    assert fresh.container.delivery_service(dry_run=False).run().cards_refreshed == 1
    completed = edition(fresh)
    fresh.repo.prune_history(
        before=fresh.clock.now() + dt.timedelta(days=1), memo_before=fresh.clock.now()
    )
    fresh.repo.restore(fresh.repo.dump())
    assert all(part.delivery is None for part in edition(fresh))
    change(fresh, "Теперь сумма 900 PLN; заявление до нового срока. " * 600)
    before = len(world.publisher.edited)

    refresh(fresh)

    current = edition(fresh)
    assert len(current) > len(completed)
    assert [part.message_id for part in current[: len(completed)]] == [
        part.message_id for part in completed
    ]
    assert len(world.publisher.edited) == before + len(completed)
    assert card(fresh).rendered_sha256 == current[0].rendered_sha256


def test_alias_growth_preserves_predecessor_targets_and_reports_only_new_physical_messages() -> (
    None
):
    world = ready_world(long=True)
    world.run()
    predecessor = card(world)
    previous = edition(world)
    bill = successor(world)
    record = bill.analysis
    assert record is not None, "the successor inherited its predecessor's analysis"
    analysis = record.analysis.model_copy(
        update={"practical_impact": "Новая сумма 900 PLN. " * 1300}
    )
    world.repo.save_analysis(TERM, bill.number, record.model_copy(update={"analysis": analysis}))
    bill = world.bill(bill.number)
    expected = world.publisher.render_card_parts(
        bill, world.gateway.get_print(TERM, bill.number), minimum=len(previous)
    )
    assert len(expected) > len(previous)
    before = len(world.publisher.sent)
    result = PublishingResult()

    assert world.container.publishing_service(dry_run=False).publish_bill(bill, result)

    alias = owned(world, bill.number)
    current = edition(world, bill.number)
    assert alias.id != predecessor.id
    assert owned(world, "3039") == predecessor
    assert [part.message_id for part in current[: len(previous)]] == [
        part.message_id for part in previous
    ]
    assert result.published == result.joined == 0
    assert result.message_parts_sent == len(expected) - len(previous)
    assert len(world.publisher.sent) == before + result.message_parts_sent
    world.repo.prune_history(
        before=world.clock.now() + dt.timedelta(days=1), memo_before=world.clock.now()
    )
    world.repo.restore(world.repo.dump())
    assert all(part.delivery is None for part in edition(world, bill.number))
    updated = analysis.model_copy(update={"practical_impact": "Только 1000 PLN."})
    world.repo.save_analysis(TERM, bill.number, record.model_copy(update={"analysis": updated}))
    before = len(world.publisher.edited)

    assert world.container.poster(dry_run=False).rerender_card(world.bill(bill.number), alias)

    final = edition(world, bill.number)
    assert len(final) == len(current)
    assert [part.message_id for part in final] == [part.message_id for part in current]
    assert len(world.publisher.edited) == before + len(current)


def test_new_multipart_revision_preserves_completed_legacy_rows_and_projection() -> None:
    legacy = saved_edit(legacy=True, multipart=True, attempted=False)
    world = legacy.world
    root = world.repo.publication_by_id(legacy.identities[0])
    assert root is not None, "the legacy reader fixture saves its physical edition"
    assert world.container.poster(dry_run=False).deliver(root)
    previous = edition(world)
    baseline = card(world)
    assert baseline.rendered_sha256 == legacy.card.rendered_sha256
    assert previous[0].delivery is not None and previous[0].delivery.revision_of is None
    change(world, "Новая подтверждённая редакция: 900 PLN. " * 500)

    refresh(world)

    current = edition(world)
    assert current[0].id != previous[0].id
    assert current[0].delivery is not None and current[0].delivery.revision_of == baseline.id
    assert [world.repo.publication_by_id(part.id or 0) for part in previous] == previous
    assert card(world).rendered_sha256 == current[0].rendered_sha256


@pytest.mark.parametrize("shape", ["single_many", "many_shrink"])
@pytest.mark.parametrize("position", ["root", "final"])
@pytest.mark.parametrize("outcome", ["definite", "outage", "uncertain"])
def test_multipart_transport_outcomes_preserve_attempts_projection_and_confirmed_message_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shape: Shape,
    position: str,
    outcome: str,
) -> None:
    case = prepare(shape=shape)
    world = case.world
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared
    index = 0 if position == "root" else len(case.texts) - 1
    called: list[int] = []

    def transport(saved: Publication) -> PublishResult:
        assert saved.id is not None, "the physical operation has its immutable identity"
        called.append(saved.id)
        if len(called) != index + 1:
            return original(saved)
        if outcome == "uncertain":
            original(saved)
            raise DeliveryUncertainError("accepted before lost response")
        if outcome == "outage":
            raise TelegramUnavailableError("test channel unavailable")
        raise RuntimeError("definite edit refusal")

    monkeypatch.setattr(world.publisher, "publish_prepared", transport)

    result = world.container.tracking_service(dry_run=False).check_bill(world.bill("3039"))

    current = edition(world)
    affected = current[index]
    confirmed_new = max(0, index - len(case.previous))
    assert len(called) == index + 1
    assert result.cards_refreshed == 0
    assert result.attention.get("continuation_messages", 0) == confirmed_new
    assert affected.status is (
        PublicationStatus.UNKNOWN if outcome == "uncertain" else PublicationStatus.FAILED
    )
    assert affected.attempts == int(outcome == "definite")
    assert card(world) == case.baseline
    assert (result.fatal_error is not None) is (position == "root" and outcome != "definite")
    assert bool(result.partial_errors) is (position == "final" and outcome != "definite")
    if position == "root":
        assert all(part.status is PublicationStatus.QUEUED for part in current[1:])
    fresh = restored(remote, world, tmp_path)
    assert edition(fresh) == current
    monkeypatch.setattr(world.publisher, "publish_prepared", original)
    before = len(world.publisher.sent) + len(world.publisher.edited)

    later = fresh.container.delivery_service(dry_run=False).run()

    if outcome == "uncertain":
        assert later.cards_refreshed == 0
        assert len(world.publisher.sent) + len(world.publisher.edited) == before
        assert card(fresh) == case.baseline
    else:
        assert later.cards_refreshed == 1
        assert not later.errors
        assert later.message_parts_sent == len(case.texts) - len(case.previous) - confirmed_new
        assert card(fresh).rendered_sha256 == current[0].rendered_sha256


@pytest.mark.parametrize("position", ["root", "final"])
def test_card_checkpoint_failure_stops_siblings_and_the_next_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, position: str
) -> None:
    case = prepare(second=True)
    world = case.world
    second = owned(world, "3040")
    record = world.bill("3040").analysis
    assert record is not None, "the production fixture completed both batches"
    world.repo.save_analysis(
        TERM,
        "3040",
        record.model_copy(
            update={
                "analysis": record.analysis.model_copy(
                    update={"practical_impact": "Только 900 PLN."}
                )
            }
        ),
    )
    remote = connect(world, tmp_path)
    at = 2 if position == "root" else len(case.texts) * 2
    reject_checkpoint(monkeypatch, at)
    before = len(world.publisher.sent) + len(world.publisher.edited)

    with pytest.raises(StateCheckpointError):
        world.run(discover=False, analyze=False)

    assert len(world.publisher.sent) + len(world.publisher.edited) == before + at // 2
    assert owned(world, "3040") == second
    assert edition(world, "3040") == [second]
    durable = World()
    durable.repo.restore(git(remote, "show", "state:state.sql"))
    assert owned(durable, "3040") == second
    assert edition(durable, "3040") == [second]


def test_dismissed_absent_growth_tail_is_not_recreated_and_confirmed_targets_survive_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = prepare()
    world = case.world
    original = world.publisher.publish_prepared
    called: list[int] = []

    def refuse_last(saved: Publication) -> PublishResult:
        assert saved.id is not None, "the physical operation has its saved identity"
        called.append(saved.id)
        if len(called) == len(case.texts):
            raise DeliveryUncertainError("request ended before a known response")
        return original(saved)

    monkeypatch.setattr(world.publisher, "publish_prepared", refuse_last)
    refresh(world)
    previous = edition(world)
    assert previous[-1].status is PublicationStatus.UNKNOWN
    assert previous[-1].message_id is None
    confirmed = [part.message_id for part in previous[:-1]]
    world.command(f"/delivery {previous[-1].id} dismiss checked absent")
    commands = world.container.command_service(dry_run=False)
    assert commands is not None, "the production fixture enables the audited inbox"
    assert commands.handle_pending(min_score=3, run_started_at=world.clock.now()).failed == 0
    monkeypatch.setattr(world.publisher, "publish_prepared", original)
    dismissed = edition(world)
    before = len(world.publisher.edited) + len(world.publisher.sent)

    refresh(world)

    assert edition(world) == dismissed
    assert len(world.publisher.edited) + len(world.publisher.sent) == before
    world.repo.prune_history(
        before=world.clock.now() + dt.timedelta(days=1), memo_before=world.clock.now()
    )
    world.repo.restore(world.repo.dump())
    change(world, "Совсем новая редакция: только 1000 PLN.")

    refresh(world)

    current = edition(world)
    assert [part.message_id for part in current] == confirmed
    assert len(current) == len(previous) - 1
    assert card(world).rendered_sha256 == current[0].rendered_sha256


@pytest.mark.parametrize("shape", ["single_many", "many_shrink"])
@pytest.mark.parametrize("action", ["retry", "confirm"])
def test_unknown_root_resolution_audits_before_any_remaining_multipart_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: Shape, action: str
) -> None:
    case = prepare(shape=shape)
    world = case.world
    remote = connect(world, tmp_path)
    reject_checkpoint(monkeypatch, 2)
    with pytest.raises(StateCheckpointError):
        refresh(world)
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    frozen = edition(fresh)
    root = frozen[0]
    assert root.status is PublicationStatus.UNKNOWN
    before = len(world.publisher.edited)
    incoming = fresh.command(
        f"/delivery {root.id} "
        + ("retry" if action == "retry" else f"confirm {case.baseline.message_id}")
    )
    commands = fresh.container.command_service(dry_run=False)
    assert commands is not None, "the production fixture enables the audited command inbox"
    assert commands.handle_pending(min_score=3, run_started_at=fresh.clock.now()).failed == 0
    assert len(world.publisher.edited) == before
    assert card(fresh) == case.baseline
    original = world.publisher.publish_prepared
    seen: list[int] = []

    def inspect(saved: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        assert durable.repo.delivery_resolutions(root.id or 0)[0].command == incoming
        index = [part.id for part in frozen].index(saved.id)
        plan = frozen[index].delivery
        assert saved.delivery is not None and plan is not None, "the whole edition was frozen"
        assert saved.delivery.rendered_text == plan.rendered_text
        assert saved.delivery.edit_message_id == plan.edit_message_id
        assert saved.delivery.today == plan.today
        assert card(durable) == case.baseline
        seen.append(saved.id or 0)
        return original(saved)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)

    result = fresh.container.delivery_service(dry_run=False).run()

    assert not result.errors
    assert result.cards_refreshed == 1
    assert result.message_parts_sent == len(case.texts) - len(case.previous)
    assert seen == [part.id for part in frozen[int(action == "confirm") :]]
    assert card(fresh).rendered_sha256 == root.rendered_sha256
    assert all(part.status is PublicationStatus.SENT for part in edition(fresh))


def test_rollover_card_growth_counts_new_parts_once_when_hidden_summary_becomes_visible() -> None:
    world = ready_world()
    world.run()
    baseline = card(world)
    record = world.bill("3039").analysis
    assert record is not None, "the production fixture completed its analysis batch"
    updated = record.analysis.model_copy(
        update={"summary": "Новая история законопроекта: 900 PLN. " * 800}
    )
    world.repo.save_analysis(TERM, "3039", record.model_copy(update={"analysis": updated}))
    assert (
        len(
            world.publisher.render_card_parts(
                world.bill("3039"), world.gateway.get_print(TERM, "3039"), minimum=1
            )
        )
        == 1
    )
    world.gateway.terms = [
        SejmTerm(num=TERM, start=dt.date(2023, 11, 13), end=dt.date(2026, 9, 6)),
        SejmTerm(num=TERM + 1, start=dt.date(2026, 9, 7), current=True),
    ]

    report = world.run(term=None, discover=False, analyze=False)

    current = edition(world)
    assert len(current) > 1
    assert current[0].delivery is not None and current[0].delivery.revision_of == baseline.id
    assert report.discontinued == 1
    assert report.message_parts_sent == len(current) - 1
    assert report.cards_refreshed == 0
    assert card(world).rendered_sha256 == current[0].rendered_sha256
    assert world.run(term=None, discover=False, analyze=False).message_parts_sent == 0

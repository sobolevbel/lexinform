import datetime as dt
from dataclasses import replace
from pathlib import Path

import pytest

from lexinform.errors import StateCheckpointError
from lexinform.models import Bill, Publication, PublicationKind, PublicationStatus
from lexinform.ports import PublishResult
from tests.harness import CHANNEL, TERM, World, act
from tests.scenario.test_card_remote_checkpoints import (
    Mode,
    connect,
    git,
    ready_world,
    reject_checkpoint,
    restored,
)


def card(world: World) -> Publication:
    found = world.publication("3039")
    assert found is not None and found.id is not None, "the batch fixture published its card"
    return found


def operation(world: World) -> Publication:
    current = card(world)
    found = world.repo.card_refresh_parts(current.id or 0)
    assert len(found) == 1 and found[0].kind is PublicationKind.MESSAGE_PART, (
        "a single card revision has one physical edit operation"
    )
    return found[0]


def change(world: World, text: str = "Не менее 200 PLN; заявление до 30 ноября.") -> None:
    record = world.bill("3039").analysis
    assert record is not None, "the production fixture completed its analysis batch"
    analysis = record.analysis.model_copy(update={"practical_impact": text})
    world.repo.save_analysis(TERM, "3039", record.model_copy(update={"analysis": analysis}))


def ready(mode: Mode = "on") -> World:
    world = ready_world(mode)
    world.run()
    change(world)
    return world


def refresh(world: World) -> int:
    return (
        world.container.tracking_service(dry_run=False)
        .check_bill(world.bill("3039"))
        .cards_refreshed
    )


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_single_card_refresh_freezes_its_remote_edit_before_api_and_receipt_after_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode
) -> None:
    world = ready(mode)
    baseline = card(world)
    remote = connect(world, tmp_path)
    expected = world.publisher.render_card_parts(
        world.bill("3039"), world.gateway.get_print(TERM, "3039"), minimum=1
    )[0]
    original = world.publisher.publish_prepared
    observed: list[Publication] = []

    def inspect(saved: Publication) -> PublishResult:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        current = operation(durable)
        assert current == saved
        assert current.status is PublicationStatus.PENDING
        assert current.channel_id == CHANNEL
        assert current.delivery is not None, "pending operations retain their immutable plans"
        assert current.delivery.rendered_text == expected
        assert current.delivery.edit_message_id == baseline.message_id
        assert current.delivery.revision_of == baseline.id
        assert current.delivery.reply_to == baseline.message_id
        assert current.delivery.continuation_parent == baseline.id
        assert current.delivery.today == world.clock.now().date()
        assert current.delivery.continuation_ids == ()
        compact = Bill.model_validate_json(current.delivery.bill_json)
        assert compact.analysis is None
        assert card(durable) == baseline
        observed.append(current)
        return original(saved)

    monkeypatch.setattr(world.publisher, "publish_prepared", inspect)
    sent_before = len(world.publisher.sent)

    assert refresh(world) == 1

    assert len(observed) == 1
    assert len(world.publisher.sent) == sent_before
    fresh = restored(remote, world, tmp_path)
    assert operation(fresh).status is PublicationStatus.SENT
    assert card(fresh) == baseline.model_copy(
        update={"rendered_sha256": observed[0].rendered_sha256}
    )
    assert world.publisher.edits[-1][0].analysis is None
    assert refresh(world) == 0
    assert len(observed) == 1


@pytest.mark.parametrize("at", [1, 2])
def test_single_card_checkpoint_failure_never_certifies_an_unconfirmed_remote_edition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at: int
) -> None:
    world = ready()
    baseline = card(world)
    remote = connect(world, tmp_path)
    edits = len(world.publisher.edited)
    reject_checkpoint(monkeypatch, at)

    with pytest.raises(StateCheckpointError):
        refresh(world)

    assert len(world.publisher.edited) == edits + int(at == 2)
    assert (card(world).rendered_sha256 != baseline.rendered_sha256) is (at == 2)
    fresh = restored(remote, world, tmp_path)
    assert card(fresh) == baseline
    if at == 2:
        fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
        saved = operation(fresh)
        assert saved.status is PublicationStatus.UNKNOWN
        assert refresh(fresh) == 0
        assert operation(fresh) == saved
        assert len(world.publisher.edited) == edits + 1
    else:
        assert fresh.repo.card_refresh_parts(baseline.id or 0) == [baseline]


def test_failed_single_card_revision_blocks_new_edition_and_retries_original_payload_once(
    tmp_path: Path,
) -> None:
    world = ready()
    baseline = card(world)
    remote = connect(world, tmp_path)
    world.publisher.fail_on.add("3039")

    assert refresh(world) == 0

    saved = operation(world)
    assert saved.status is PublicationStatus.FAILED and saved.attempts == 1
    assert card(world) == baseline
    change(world, "Новый текущий текст: только 300 PLN.")
    assert refresh(world) == 0
    assert operation(world) == saved
    world.publisher.fail_on.clear()
    fresh = restored(remote, world, tmp_path)
    assert operation(fresh) == saved

    first = fresh.container.delivery_service(dry_run=False).run()
    second = fresh.container.delivery_service(dry_run=False).run()

    assert first.cards_refreshed == 1 and second.cards_refreshed == 0
    assert (
        fresh.publisher.edited[-1][0].text == saved.delivery.rendered_text
        if saved.delivery
        else False
    )
    assert card(fresh).rendered_sha256 == saved.rendered_sha256
    assert operation(fresh).id == saved.id


@pytest.mark.parametrize("resolution", ["confirm", "dismiss"])
def test_unknown_single_card_revision_blocks_new_editions_until_audited_resolution(
    tmp_path: Path, resolution: str
) -> None:
    world = ready()
    baseline = card(world)
    connect(world, tmp_path)
    world.publisher.unknown_on_edit.add("3039")

    assert refresh(world) == 0

    saved = operation(world)
    assert saved.status is PublicationStatus.UNKNOWN
    assert card(world) == baseline
    assert refresh(world) == 0
    assert operation(world) == saved
    assert world.container.delivery_service(dry_run=False).run().cards_refreshed == 0
    suffix = f"confirm {baseline.message_id}" if resolution == "confirm" else "dismiss checked"
    incoming = world.command(f"/delivery {saved.id} {suffix}")
    commands = world.container.command_service(dry_run=False)
    assert commands is not None, "the production fixture enables the audited command inbox"
    world.publisher.unknown_on_edit.clear()

    answer = commands.handle_pending(min_score=3, run_started_at=world.clock.now())

    assert answer.failed == 0
    assert world.repo.delivery_resolutions(saved.id or 0)[0].command == incoming
    assert world.container.delivery_service(dry_run=False).run().cards_refreshed == 0
    assert refresh(world) == 0
    assert card(world).rendered_sha256 == (
        saved.rendered_sha256 if resolution == "confirm" else baseline.rendered_sha256
    )
    if resolution == "dismiss":
        change(world, "Новая редакция: 300 PLN.")
        assert refresh(world) == 1
        assert operation(world).id != saved.id
        dismissed = world.repo.publication_by_id(saved.id or 0)
        assert dismissed is not None, "dismissed physical operations retain their identities"
        assert dismissed.status is PublicationStatus.DISMISSED


def test_a_card_can_return_to_a_prior_edition_without_reusing_an_attempted_identity() -> None:
    world = ready()
    identities: list[int | None] = []
    digests: list[str | None] = []
    for text in ("200 PLN.", "300 PLN.", "200 PLN.", "300 PLN."):
        change(world, text)
        assert refresh(world) == 1
        identities.append(operation(world).id)
        digests.append(card(world).rendered_sha256)
    assert len(set(identities)) == 4
    assert digests[0] == digests[2]
    assert digests[1] == digests[3]


def test_single_card_revision_saves_the_readers_day_from_the_shared_clock() -> None:
    world = ready()
    world.clock.current = dt.datetime(2026, 9, 19, 23, 30, tzinfo=dt.UTC)

    assert refresh(world) == 1

    saved = operation(world)
    assert saved.delivery is not None, "single edits freeze their local reader day"
    assert saved.delivery.today == dt.date(2026, 9, 20)
    assert saved.rendered_sha256 == world.publisher.card_digest(
        world.bill("3039"), today=dt.date(2026, 9, 20)
    )


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("guard", ["no_publish", "dry_run"])
def test_disabled_single_card_refresh_never_creates_work_or_checkpoints(
    tmp_path: Path, mode: Mode, guard: str
) -> None:
    world = ready(mode)
    baseline = card(world)
    remote = connect(world, tmp_path)
    revision = git(remote, "rev-parse", "state")
    before = len(world.publisher.edited)

    if guard == "dry_run":
        world.container = replace(world.container, publisher_override=None)
        world.pipeline = world.container.pipeline(dry_run=True)
        world.run(dry_run=True)
    else:
        world.run(publish=False)

    assert card(world) == baseline
    assert world.repo.card_refresh_parts(baseline.id or 0) == [baseline]
    assert len(world.publisher.edited) == before
    assert git(remote, "rev-parse", "state") == revision


@pytest.mark.parametrize(
    "status",
    [
        PublicationStatus.QUEUED,
        PublicationStatus.PENDING,
        PublicationStatus.FAILED,
        PublicationStatus.UNKNOWN,
    ],
)
def test_active_single_revision_blocks_another_refresh_operation(
    status: PublicationStatus,
) -> None:
    world = ready()
    baseline = card(world)
    world.publisher.fail_on.add("3039")
    assert refresh(world) == 0
    saved = operation(world)
    world.repo.mark_publication(saved.id or 0, status, count_attempt=False)
    active = operation(world)
    world.publisher.fail_on.clear()
    change(world, "Новое состояние: 300 PLN.")
    before = len(world.publisher.edited)

    assert refresh(world) == 0

    assert operation(world) == active
    assert card(world) == baseline
    assert len(world.publisher.edited) == before


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_dismissed_single_edit_stays_stopped_after_terminal_payload_retention(
    mode: Mode,
) -> None:
    world = ready(mode)
    world.repo.save_act(TERM, "3039", act(entry_into_force=dt.date(2026, 9, 1)))
    baseline = card(world)
    world.publisher.unknown_on_edit.add("3039")
    assert refresh(world) == 0
    saved = operation(world)
    world.publisher.unknown_on_edit.clear()
    incoming = world.command(f"/delivery {saved.id} dismiss checked")
    commands = world.container.command_service(dry_run=False)
    assert commands is not None, "the production fixture enables audited delivery resolutions"
    assert commands.handle_pending(min_score=3, run_started_at=world.clock.now()).failed == 0
    assert world.repo.delivery_resolutions(saved.id or 0)[0].command == incoming
    world.clock.advance(days=91)

    world.repo.prune_history(
        before=world.clock.now() - dt.timedelta(days=90), memo_before=world.clock.now()
    )

    original = card(world)
    stopped = operation(world)
    assert original.delivery is None, "terminal retention expires the sent original card payload"
    assert original.rendered_sha256 == baseline.rendered_sha256
    assert stopped.status is PublicationStatus.DISMISSED
    assert stopped.delivery is not None, "dismissed roots retain the qualified stopped edition"
    assert saved.rendered_sha256 == world.publisher.card_digest(world.bill("3039"))
    before = len(world.publisher.edited)
    assert refresh(world) == 0
    assert refresh(world) == 0
    assert operation(world) == stopped
    assert len(world.publisher.edited) == before
    record = world.bill("3039").analysis
    assert record is not None, "terminal payload retention preserves the bill analysis"
    world.repo.save_analysis(
        TERM,
        "3039",
        record.model_copy(
            update={
                "analysis": record.analysis.model_copy(
                    update={"summary": "Новая редакция после очистки: 900 PLN."}
                )
            }
        ),
    )
    assert world.publisher.card_digest(world.bill("3039")) != stopped.rendered_sha256

    assert refresh(world) == 1

    assert operation(world).id != saved.id
    assert len(world.publisher.edited) == before + 1
    assert world.repo.publication_by_id(saved.id or 0) == stopped

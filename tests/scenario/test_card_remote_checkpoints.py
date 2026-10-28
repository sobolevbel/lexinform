import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from lexinform.adapters.batch_checkpoint import GitBatchCheckpoint
from lexinform.errors import DeliveryUncertainError, StateCheckpointError
from lexinform.models import Publication, PublicationKind, PublicationStatus
from lexinform.ports import PublishResult
from lexinform.services.analysis import AnalysisService
from tests.checkpoint_state import saved_state
from tests.fakes import make_analysis, make_comparison
from tests.harness import CHANNEL, TERM, World

Mode = Literal["off", "shadow", "on"]
TITLE = "Projekt ustawy o cudzoziemcach"


class RunnerLost(BaseException):
    pass


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def ready_world(
    mode: Mode = "on",
    kind: PublicationKind = PublicationKind.NEW_BILL,
    *,
    long: bool = False,
    second: bool = False,
) -> World:
    analysis = make_analysis()
    if long:
        analysis.practical_impact = "Старые заявления не подавать заново; 200 PLN. " * 220
    world = World(profile="production", reader_attention=mode, batch_script={"3039": analysis})
    world.batch.secondary_script[("3039", "joint")] = make_comparison(
        differences=["Новый срок для подачи заявления. " * 220] if long else None
    )
    if kind is PublicationKind.JOINT_BILL:
        world.add_bill("3038", TITLE)
        world.run()
        world.batch.resolve()
        world.run()
    world.add_bill("3039", TITLE)
    if second:
        world.add_bill("3040", TITLE)
    if kind is PublicationKind.JOINT_BILL:
        world.touch("3039", world.clock.now(), prints_considered_jointly=("3038",))
        world.touch("3038", world.clock.now(), prints_considered_jointly=("3039",))
    world.run(max_publish=0)
    world.batch.resolve()
    world.run(max_publish=0)
    if kind is PublicationKind.JOINT_BILL:
        waiting = world.container.publishing_service(dry_run=False).publish_new(
            min_score=3, limit=10, may_wait=True
        )
        assert waiting.waiting == 1, "the production joint comparison waits for its batch"
        world.run(max_publish=0)
        world.batch.resolve()
        world.run(max_publish=0)
    assert world.publication("3039", kind) is None
    assert not world.llm.contexts
    return world


def connect(world: World, tmp_path: Path) -> Path:
    remote, local = tmp_path / "remote.git", tmp_path / "state"
    remote.mkdir()
    local.mkdir()
    git(remote, "init", "--bare")
    git(local, "init", "-b", "state")
    git(local, "remote", "add", "origin", str(remote))
    world.container = replace(
        world.container,
        settings=world.container.settings.model_copy(
            update={
                "llm_batch_state_file": local / "state.sql",
                "state_checkpoint_required": True,
            }
        ),
    )
    world.container.state_checkpoint()()
    world.pipeline = world.container.pipeline(dry_run=False)
    return remote


def restored(remote: Path, world: World, tmp_path: Path) -> World:
    clone = tmp_path / "restored"
    git(tmp_path, "clone", "--branch", "state", str(remote), str(clone))
    fresh = World(profile="production", reader_attention=world.container.settings.reader_attention)
    fresh.repo.restore((clone / "state.sql").read_text())
    for number in ("3038", "3039", "3040"):
        if fresh.repo.get(TERM, number) is not None:
            fresh.add_bill(number, TITLE)
    fresh.publisher = world.publisher
    fresh.container = replace(
        fresh.container,
        publisher_override=fresh.publisher,
        settings=fresh.container.settings.model_copy(
            update={
                "llm_batch_state_file": clone / "state.sql",
                "state_checkpoint_required": True,
            }
        ),
    )
    fresh.pipeline = fresh.container.pipeline(dry_run=False)
    return fresh


def reject_checkpoint(monkeypatch: pytest.MonkeyPatch, at: int) -> list[int]:
    original = GitBatchCheckpoint.__call__
    calls: list[int] = []

    def checkpoint(writer: GitBatchCheckpoint) -> None:
        calls.append(len(calls) + 1)
        if len(calls) == at:
            raise StateCheckpointError("injected reader checkpoint failure")
        original(writer)

    monkeypatch.setattr(GitBatchCheckpoint, "__call__", checkpoint)
    return calls


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("kind", [PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL])
def test_every_card_part_has_a_remote_pending_payload_and_exact_parent_before_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode, kind: PublicationKind
) -> None:
    world = ready_world(mode, kind, long=True)
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared
    identities: list[int] = []

    def send(publication: Publication) -> PublishResult:
        with saved_state(git(remote, "show", "state:state.sql")) as durable:
            row = durable.repo.publication_by_id(publication.id or 0)
            assert row is not None and row.delivery is not None, "remote intent precedes the API"
            assert row.status is PublicationStatus.PENDING
            assert row.channel_id == CHANNEL
            assert publication.delivery is not None, "prepared sends carry frozen HTML"
            assert row.delivery.rendered_text == publication.delivery.rendered_text
            assert row.delivery.reply_to == publication.delivery.reply_to
            if row.delivery.continuation_parent is not None:
                parent = durable.repo.publication_by_id(row.delivery.continuation_parent)
                assert parent is not None and parent.status is PublicationStatus.SENT
                assert row.delivery.reply_to == parent.message_id
            else:
                assert row.delivery.continuation_ids, "the long fixture has multiple physical parts"
                for identity in row.delivery.continuation_ids:
                    child = durable.repo.publication_by_id(identity)
                    assert child is not None and child.delivery is not None
                    assert child.status is PublicationStatus.QUEUED and child.delivery.rendered_text
            identities.append(publication.id or 0)
            return original(publication)

    monkeypatch.setattr(world.publisher, "publish_prepared", send)

    result = world.container.publishing_service(dry_run=False).publish_new(min_score=3, limit=10)

    assert result.published + result.joined == 1
    assert len(identities) >= 3
    fresh = restored(remote, world, tmp_path)
    for identity in identities:
        row = fresh.repo.publication_by_id(identity)
        assert row is not None and row.status is PublicationStatus.SENT and row.message_id
    assert git(remote, "show", "state:pending-batches.json").startswith('{"version":1,')
    assert not world.llm.contexts


@pytest.mark.parametrize("kind", [PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL])
@pytest.mark.parametrize("at", [1, 2])
def test_card_checkpoint_failure_stops_later_cards_and_survives_loss_of_local_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: PublicationKind, at: int
) -> None:
    world = ready_world(kind=kind, second=kind is PublicationKind.NEW_BILL)
    remote = connect(world, tmp_path)
    before = len(world.publisher.sent)
    calls = reject_checkpoint(monkeypatch, at)

    with pytest.raises(StateCheckpointError, match="reader checkpoint failure"):
        world.run(track=False)

    assert len(world.publisher.sent) - before == int(at == 2)
    assert calls == list(range(1, at + 1))
    assert world.notifier.calls[-1][0].finished_at is not None
    assert "digest" not in world.notifier.calls[-1][0].phase_seconds
    local = world.publication("3039", kind)
    assert local is not None and local.attempts == 0
    assert local.status is (PublicationStatus.SENT if at == 2 else PublicationStatus.PENDING)
    fresh = restored(remote, world, tmp_path)
    if at == 1:
        assert fresh.publication("3039", kind) is None
    else:
        fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
        row = fresh.publication("3039", kind)
        assert row is not None and row.status is PublicationStatus.UNKNOWN
        assert not fresh.container.publishing_service(dry_run=False).publish_bill(
            fresh.bill("3039")
        )
        assert len(world.publisher.sent) - before == 1


def test_lost_runner_after_pending_checkpoint_never_resends_the_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = ready_world()
    remote = connect(world, tmp_path)

    def lost(publication: Publication) -> PublishResult:
        raise RunnerLost()

    monkeypatch.setattr(world.publisher, "publish_prepared", lost)
    with pytest.raises(RunnerLost):
        world.container.publishing_service(dry_run=False).publish_new(min_score=3, limit=10)
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())

    result = fresh.container.publishing_service(dry_run=False).publish_new(min_score=3, limit=10)

    assert result.published == 0 and not world.publisher.sent
    row = fresh.publication("3039")
    assert row is not None and row.status is PublicationStatus.UNKNOWN


def test_accepted_card_with_lost_response_keeps_a_durable_unknown_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = ready_world()
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared

    def uncertain(publication: Publication) -> PublishResult:
        original(publication)
        raise DeliveryUncertainError("accepted but response lost")

    monkeypatch.setattr(world.publisher, "publish_prepared", uncertain)

    result = world.container.publishing_service(dry_run=False).publish_new(min_score=3, limit=10)

    assert result.fatal_error and len(world.publisher.sent) == 1
    fresh = restored(remote, world, tmp_path)
    row = fresh.publication("3039")
    assert row is not None and row.status is PublicationStatus.UNKNOWN and row.attempts == 0
    assert not fresh.repo.list_due_deliveries(CHANNEL, max_attempts=3)


@pytest.mark.parametrize("at", [2, 4])
def test_multipart_receipt_failure_blocks_all_later_physical_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at: int
) -> None:
    world = ready_world(long=True)
    remote = connect(world, tmp_path)
    reject_checkpoint(monkeypatch, at)

    with pytest.raises(StateCheckpointError):
        world.run(track=False)

    assert len(world.publisher.sent) == at // 2
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    root = fresh.publication("3039")
    assert root is not None and root.delivery is not None
    assert root.status is (PublicationStatus.UNKNOWN if at == 2 else PublicationStatus.SENT)
    first = fresh.repo.publication_by_id(root.delivery.continuation_ids[0])
    assert first is not None and first.delivery is not None
    assert first.status is (PublicationStatus.QUEUED if at == 2 else PublicationStatus.UNKNOWN)
    before = len(world.publisher.sent)

    fresh.container.delivery_service(dry_run=False).run()

    assert len(world.publisher.sent) == before


def test_delivery_drain_does_not_swallow_a_card_continuation_checkpoint_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = ready_world(long=True)
    world.publisher.fail_continuation_attempt = 1
    remote = connect(world, tmp_path)
    world.container.publishing_service(dry_run=False).publish_new(min_score=3, limit=10)
    fresh = restored(remote, world, tmp_path)
    fresh.publisher.fail_continuation_attempt = None
    before = len(world.publisher.sent)
    reject_checkpoint(monkeypatch, 1)

    with pytest.raises(StateCheckpointError, match="reader checkpoint failure"):
        fresh.container.delivery_service(dry_run=False).run()

    assert len(world.publisher.sent) == before
    assert fresh.notifier.calls[-1][0].finished_at is not None
    assert any("state persistence failed" in error for error in fresh.notifier.calls[-1][0].errors)


def test_saved_parent_receipt_allows_only_unstarted_parts_after_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = ready_world(long=True)
    remote = connect(world, tmp_path)
    reject_checkpoint(monkeypatch, 3)
    with pytest.raises(StateCheckpointError):
        world.run(track=False)
    assert len(world.publisher.sent) == 1
    fresh = restored(remote, world, tmp_path)
    root = fresh.publication("3039")
    assert root is not None and root.delivery is not None and root.message_id is not None
    assert root.status is PublicationStatus.SENT
    before = len(world.publisher.sent)

    fresh.container.delivery_service(dry_run=False).run()

    assert len(world.publisher.texts(PublicationKind.NEW_BILL)) == 1
    assert len(world.publisher.sent) - before == len(root.delivery.continuation_ids)
    first = fresh.repo.publication_by_id(root.delivery.continuation_ids[0])
    assert first is not None and first.delivery is not None
    assert first.status is PublicationStatus.SENT and first.delivery.reply_to == root.message_id


@pytest.mark.parametrize("kind", [PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL])
def test_operator_retry_is_checkpointed_with_its_audit_before_the_card_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: PublicationKind
) -> None:
    world = ready_world(kind=kind)
    remote = connect(world, tmp_path)
    original = world.publisher.publish_prepared

    def lost(publication: Publication) -> PublishResult:
        raise RunnerLost()

    monkeypatch.setattr(world.publisher, "publish_prepared", lost)
    with pytest.raises(RunnerLost):
        world.container.publishing_service(dry_run=False).publish_new(min_score=3, limit=10)
    fresh = restored(remote, world, tmp_path)
    fresh.repo.mark_stale_pending_as_unknown(now=fresh.clock.now())
    row = fresh.publication("3039", kind)
    assert row is not None and row.id is not None and row.delivery is not None
    plan = row.delivery
    incoming = fresh.command(f"/delivery {row.id} retry")
    observed: list[int] = []

    def retry(publication: Publication) -> PublishResult:
        with saved_state(git(remote, "show", "state:state.sql")) as durable:
            assert publication.id is not None, "the retry preserves the original publication id"
            audit = durable.repo.delivery_resolutions(publication.id)
            assert len(audit) == 1 and audit[0].command == incoming
            saved = durable.repo.publication_by_id(publication.id)
            assert saved is not None and saved.delivery is not None
            assert saved.status is PublicationStatus.PENDING
            assert saved.delivery.rendered_text == plan.rendered_text
            assert saved.delivery.reply_to == plan.reply_to
            observed.append(publication.id)
            return original(publication)

    monkeypatch.setattr(fresh.publisher, "publish_prepared", retry)

    fresh.run(track=False)

    assert observed == [row.id]
    with saved_state(git(remote, "show", "state:state.sql")) as durable:
        receipt = durable.repo.publication_by_id(row.id)
        assert receipt is not None and receipt.status is PublicationStatus.SENT


@pytest.mark.parametrize("dry_run", [False, True])
def test_ready_card_dry_run_and_no_publish_do_not_push_or_send(
    tmp_path: Path, dry_run: bool
) -> None:
    world = ready_world()
    remote = connect(world, tmp_path)
    baseline = git(remote, "rev-parse", "state")
    if dry_run:
        world.container = replace(world.container, publisher_override=None)
    world.pipeline = world.container.pipeline(dry_run=dry_run)

    world.run(dry_run=dry_run, publish=dry_run, track=False)

    assert git(remote, "rev-parse", "state") == baseline
    assert not world.publisher.sent


def test_a_foreign_state_writer_stops_the_card_before_its_api(tmp_path: Path) -> None:
    world = ready_world()
    remote = connect(world, tmp_path)
    rival = tmp_path / "rival"
    git(tmp_path, "clone", "--branch", "state", str(remote), str(rival))
    with (rival / "state.sql").open("a") as stream:
        stream.write("\n-- foreign writer\n")
    git(rival, "add", "state.sql")
    git(
        rival,
        "-c",
        "user.name=Rival",
        "-c",
        "user.email=rival@example.test",
        "commit",
        "-m",
        "state: foreign checkpoint",
    )
    git(rival, "push", "origin", "HEAD:refs/heads/state")
    foreign = git(remote, "rev-parse", "state")

    with pytest.raises(StateCheckpointError, match="remote baseline changed"):
        world.run(track=False)

    assert not world.publisher.sent
    assert git(remote, "rev-parse", "state") == foreign


def test_joint_comparison_cannot_hide_a_typed_persistence_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = ready_world(kind=PublicationKind.JOINT_BILL)
    connect(world, tmp_path)
    before = len(world.publisher.sent)

    def failed(*args: object, **kwargs: object) -> None:
        raise StateCheckpointError("joint persistence failed")

    monkeypatch.setattr(AnalysisService, "compare_joint", failed)

    with pytest.raises(StateCheckpointError, match="joint persistence failed"):
        world.run(track=False)

    assert len(world.publisher.sent) == before
    assert world.publication("3039", PublicationKind.JOINT_BILL) is None

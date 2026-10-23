import datetime as dt
from dataclasses import replace

import pytest

from lexinform.models import BillStatus, Publication, PublicationKind, PublicationStatus
from lexinform.ports import PublishResult
from tests.fakes import make_analysis
from tests.harness import COMMITTEE_STAGES, TERM, World

TITLE = "Projekt ustawy o cudzoziemcach"


def _restart(world: World) -> None:
    world.repo.restore(world.repo.dump())
    world.container = replace(world.container)
    world.discovery = world.container.discovery_service()
    world.analysis = world.container.analysis_service()
    world.tracking = world.container.tracking_service(dry_run=False)
    world.pipeline = world.container.pipeline(dry_run=False)


def test_production_profile_requires_batches_without_changing_the_default() -> None:
    ordinary = World()
    production = World(profile="production", batch=False, batch_only=False)

    assert not ordinary.container.analysis_options().require_batch
    assert production.container.analysis_options().require_batch


@pytest.mark.parametrize("workers", [1, 4])
def test_pending_analysis_restores_without_submission_or_synchronous_fallback(workers: int) -> None:
    world = World(profile="production", workers=workers)
    for number in ("3039", "3040"):
        world.add_bill(number, TITLE)

    world.run()

    assert [request.number for request in world.batch.submitted] == ["3039", "3040"]
    assert all(world.bill(number).status is BillStatus.BATCH_PENDING for number in ("3039", "3040"))
    assert not world.publisher.new_bills and not world.llm.contexts
    _restart(world)
    world.clock.advance(hours=48)
    waiting = world.run()
    assert waiting.published == 0
    assert len(world.batch.submitted) == 2 and not world.llm.contexts
    world.batch.resolve()

    completed = world.run()
    _restart(world)
    repeated = world.run()

    assert (completed.published, repeated.published) == (2, 0)
    assert [bill.number for bill, _ in world.publisher.new_bills] == ["3039", "3040"]
    assert len(world.batch.submitted) == 2 and not world.llm.contexts


@pytest.mark.parametrize("workers", [1, 4])
def test_old_batch_answer_cannot_complete_a_replacement_generation_after_restore(
    workers: int,
) -> None:
    world = World(profile="production", workers=workers)
    world.add_bill("3039", TITLE)
    world.run()
    original_generation = world.bill("3039").analysis_generation
    world.repo.reset_bill(TERM, "3039", BillStatus.ANALYSIS_PENDING)
    world.run()
    replacement_generation = world.bill("3039").analysis_generation
    assert replacement_generation > original_generation
    _restart(world)
    world.batch.resolve("batch-1")

    stale = world.run()

    assert stale.llm_input_tokens == 100
    assert world.bill("3039").status is BillStatus.BATCH_PENDING
    assert world.bill("3039").analysis_generation == replacement_generation
    assert world.bill("3039").analysis is None and not world.publisher.new_bills
    _restart(world)
    world.batch.resolve("batch-2")
    completed = world.run()
    repeated = world.run()
    assert (completed.published, repeated.published) == (1, 0)
    assert len(world.batch.submitted) == 2 and not world.llm.contexts


@pytest.mark.parametrize("workers", [1, 4])
def test_secondary_batch_preserves_observation_and_delivery_across_restore(workers: int) -> None:
    world = World(profile="production", workers=workers)
    world.add_bill("3039", TITLE)
    world.run()
    world.batch.resolve()
    world.run()
    before = world.bill("3039").observed_process
    world.file_to_print("3039", "Do druku nr 3039 - ocena skutków regulacji")
    world.clock.advance(hours=1)

    waiting = world.run()

    assert waiting.batch_waiting == 1
    assert world.bill("3039").observed_process == before
    assert world.bill("3039").seen_supplements == ()
    assert world.bill("3039").awaiting_batch_since is not None
    assert world.publication("3039", PublicationKind.STATUS_UPDATE) is None
    _restart(world)
    world.set_stages("3039", COMMITTEE_STAGES)
    world.clock.advance(hours=48)
    world.run(since=world.clock.now())
    assert not world.publisher.updates and not world.llm.supplement_contexts
    assert len(world.batch.submitted) == 2
    world.batch.resolve()

    completed = world.run(since=world.clock.now())
    _restart(world)
    repeated = world.run(since=world.clock.now())

    assert (completed.updates, repeated.updates) == (1, 0)
    assert world.bill("3039").awaiting_batch_since is None
    assert world.bill("3039").seen_supplements == ("3039-s",)
    change = world.publisher.updates[0][1]
    assert len(change.new_stages) == 2
    assert len(change.supplements) == 1 and change.supplements[0].digest is not None
    assert not world.llm.contexts and not world.llm.supplement_contexts


@pytest.mark.parametrize("workers", [1, 4])
def test_failed_card_reuses_frozen_analysis_after_restore(workers: int) -> None:
    world = World(profile="production", workers=workers, fail_publish={"3039"})
    world.add_bill("3039", TITLE)
    world.run()
    world.batch.resolve()
    world.run()
    failed = world.publication("3039")
    assert failed is not None and failed.delivery is not None, "a failed card retains its payload"
    snapshot = failed.delivery.bill_json
    record = world.bill("3039").analysis
    assert record is not None, "batch collection saved the analysis before attempting delivery"
    changed = record.analysis.model_copy(update={"summary": "Позднейшее содержание"})
    world.repo.save_analysis(TERM, "3039", record.model_copy(update={"analysis": changed}))
    _restart(world)
    world.publisher.fail_on.clear()

    completed = world.run()
    repeated = world.run()

    assert (completed.published, repeated.published) == (1, 0)
    assert world.publisher.new_bills[0][0].model_dump_json() == snapshot
    assert len(world.batch.submitted) == 1 and not world.llm.contexts


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("accepted", [False, True])
def test_interrupted_card_is_unknown_after_restore_and_never_retried(
    workers: int, accepted: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(profile="production", workers=workers)
    world.add_bill("3039", TITLE)
    world.run()
    world.batch.resolve()
    send = world.publisher.publish_prepared

    def interrupted(publication: Publication) -> PublishResult:
        if accepted:
            send(publication)
        raise KeyboardInterrupt("runner stopped before recording the delivery result")

    with monkeypatch.context() as patch:
        patch.setattr(world.publisher, "publish_prepared", interrupted)
        with pytest.raises(KeyboardInterrupt):
            world.run()
    pending = world.publication("3039")
    assert pending is not None and pending.delivery is not None, "delivery is frozen before sending"
    assert pending.status is PublicationStatus.PENDING
    frozen = pending.delivery
    _restart(world)

    world.run()
    world.run()

    unknown = world.publication("3039")
    assert unknown is not None, "the saved pending publication retains its identity after restore"
    assert unknown.id == pending.id and unknown.status is PublicationStatus.UNKNOWN
    assert unknown.delivery == frozen
    assert len(world.publisher.new_bills) == int(accepted)
    assert len(world.batch.submitted) == 1 and not world.llm.contexts


@pytest.mark.parametrize("workers", [1, 4])
def test_uncertain_continuation_keeps_payload_and_blocks_automatic_retry(workers: int) -> None:
    analysis = make_analysis()
    analysis.practical_impact = "Старые заявления не подавать заново; 200 PLN. " * 220
    world = World(
        profile="production",
        workers=workers,
        reader_attention="on",
        batch_script={"3039": analysis},
    )
    world.add_bill("3039", TITLE)
    world.publisher.unknown_continuation_attempt = 1
    world.run()
    world.batch.resolve()
    world.run()
    root = world.publication("3039")
    assert root is not None and root.delivery is not None, (
        "unfinished multipart delivery is retained"
    )
    parts = root.delivery.continuation_ids
    assert len(parts) >= 2
    frozen = {}
    for identity in parts:
        row = world.repo.publication_by_id(identity)
        assert row is not None and row.delivery is not None, (
            "each continuation has a frozen payload"
        )
        frozen[identity] = row.delivery.rendered_text
    _restart(world)
    world.clock.advance(days=2)
    world.publisher.unknown_continuation_attempt = None

    world.run()
    world.run()

    assert world.publisher.continuation_attempts == 1
    assert len(world.publisher.texts(PublicationKind.NEW_BILL)) == 1
    for index, identity in enumerate(parts):
        row = world.repo.publication_by_id(identity)
        assert row is not None and row.delivery is not None, (
            "blocked continuations remain recoverable"
        )
        assert row.status is (PublicationStatus.UNKNOWN if index == 0 else PublicationStatus.QUEUED)
        assert row.delivery.rendered_text == frozen[identity]
    assert not world.llm.contexts


def test_production_profile_is_worker_count_independent() -> None:
    outputs = []
    for workers in (1, 4):
        world = World(profile="production", workers=workers)
        for number in ("3039", "3040", "3041"):
            world.add_bill(number, TITLE)
        world.run()
        _restart(world)
        world.batch.resolve()
        world.run()
        world.set_stages("3039", COMMITTEE_STAGES)
        world.run()
        outputs.append(
            (
                [request.custom_id for request in world.batch.submitted],
                world.publisher.texts(PublicationKind.NEW_BILL),
                world.publisher.texts(PublicationKind.STATUS_UPDATE),
            )
        )

    assert len(outputs[0][1]) == 3 and len(outputs[0][2]) == 1
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize("workers", [1, 4])
def test_production_batches_feed_daily_delivery_after_restore_at_the_configured_hour(
    workers: int,
) -> None:
    world = World(
        profile="production", workers=workers, reader_attention="on", attention_roundup="daily"
    )
    for number in ("3039", "3040"):
        world.add_bill(number, TITLE)
    world.run()
    assert len(world.batch.submitted) == 2 and not world.publisher.sent
    _restart(world)
    world.batch.resolve()

    cards = world.run()

    assert cards.published == 2
    assert len(world.publisher.texts(PublicationKind.NEW_BILL)) == 2
    world.set_stages("3039", COMMITTEE_STAGES)
    first = world.run()
    assert first.updates == 0 and first.attention["daily_queued"] == 1
    queued = world.publication("3039", PublicationKind.STATUS_UPDATE)
    assert queued is not None and queued.delivery is not None, (
        "the observed update owns its delivery"
    )
    assert queued.delivery.schedule is not None, (
        "daily mode saves the delivery window before release"
    )
    assert queued.delivery.schedule.not_before == dt.datetime(2026, 9, 7, 16, tzinfo=dt.UTC)
    assert queued.status is PublicationStatus.QUEUED and queued.attempts == 0
    world.clock.advance(hours=5)
    world.set_stages("3040", COMMITTEE_STAGES)
    assert world.run().updates == 0
    _restart(world)
    world.clock.advance(hours=4, minutes=59)
    assert world.container.delivery_service(dry_run=False).run().updates == 0
    calls = list(world.gateway.calls)
    world.clock.advance(minutes=1)

    released = world.container.delivery_service(dry_run=False).run()
    _restart(world)
    repeated = world.container.delivery_service(dry_run=False).run()

    assert released.ok and (released.updates, released.update_events) == (1, 2)
    assert released.attention["daily_released"] == 2 and repeated.updates == 0
    assert len(world.publisher.texts(PublicationKind.STATUS_UPDATE)) == 1
    assert world.gateway.calls == calls and len(world.batch.submitted) == 2
    assert not world.llm.contexts
    for number in ("3039", "3040"):
        sent = world.publication(number, PublicationKind.STATUS_UPDATE)
        assert sent is not None and sent.delivery is not None, (
            "each released event retains its owner"
        )
        assert sent.status is PublicationStatus.SENT and len(sent.delivery.update_group) == 2

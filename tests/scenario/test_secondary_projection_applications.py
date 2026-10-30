import pytest

from lexinform.models import (
    DocumentReviewRecord,
    ResultApplication,
    ReviewPurpose,
    ReviewWork,
    RunMode,
    StatusChange,
)
from lexinform.services.tracking_result import TrackingResult
from tests.harness import World
from tests.scenario.test_amendments_command import published_world
from tests.scenario.test_explain import commands_only
from tests.scenario.test_joint_batch import joint_world
from tests.scenario.test_review_computation_history import queued_review, submit_review


def applications(world: World, number: str = "3039") -> list[ResultApplication]:
    return [
        row for row in world.repo.list_result_applications(10, number) if row.target is not None
    ]


def review_world(purpose: ReviewPurpose, *, workers: int = 1) -> tuple[World, ReviewWork]:
    world, source, intent = queued_review(purpose, workers=workers)
    submit_review(world, intent)
    world.batch.resolve()
    world.analysis.collect_batches()
    world.analysis.start_run()
    work = ReviewWork(
        memo_key=source.memo_key(10),
        purpose=purpose,
        context=source,
        source_key="source/1",
        since=world.clock.now(),
        baseline_day=world.clock.now().date(),
    )
    world.repo.save_review_work(10, "3039", (work,))
    assert not applications(world)
    return world, work


def stored_review(world: World, purpose: ReviewPurpose) -> DocumentReviewRecord | None:
    bill = world.bill("3039")
    return bill.consultation_review if purpose == "consultation_review" else bill.commission_review


@pytest.mark.parametrize("purpose", ["consultation_review", "commission_review"])
@pytest.mark.parametrize("workers", [1, 4])
def test_one_paid_review_applies_to_two_source_scopes(purpose: ReviewPurpose, workers: int) -> None:
    world, work = review_world(purpose, workers=workers)
    reviews = world.container.document_reviews(dry_run=False)
    history = world.repo.list_computation_results(10, "3039")
    before = world.bill("3039")

    reviews.advance(TrackingResult(), publish=False)
    world.repo.restore(world.repo.dump())
    world.clock.advance(days=1)
    world.repo.save_review_work(10, "3039", (work.model_copy(update={"source_key": "source/2"}),))
    reviews.advance(TrackingResult(), publish=False)
    world.repo.restore(world.repo.dump())
    reviews.advance(TrackingResult(), publish=False)

    rows = applications(world)
    record = stored_review(world, purpose)
    assert record is not None, "advance stores the completed review on its bill"
    assert len(rows) == 2 and all(row.result_id == record.computation_id for row in rows)
    assert all(row.target is not None and row.target.kind == "document_review" for row in rows)
    assert {row.target.ref for row in rows if row.target is not None} == {
        f"{purpose}/source/1",
        f"{purpose}/source/2",
    }
    assert all(row.generation is None for row in rows)
    assert record.source_key == "source/2" and not world.bill("3039").review_work
    assert world.bill("3039").analysis == before.analysis
    assert world.bill("3039").analysis_generation == before.analysis_generation
    assert world.repo.list_computation_results(10, "3039") == history
    assert len(world.batch.submitted) == 1


@pytest.mark.parametrize("purpose", ["consultation_review", "commission_review"])
def test_review_projection_receipt_and_work_completion_roll_back_together(
    purpose: ReviewPurpose, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, work = review_world(purpose)
    reviews = world.container.document_reviews(dry_run=False)

    def crash(application: ResultApplication) -> None:
        raise RuntimeError("injected review application failure")

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, "save_result_application", crash)
        result = TrackingResult()
        reviews.advance(result, publish=False)

    assert result.partial_errors and stored_review(world, purpose) is None
    assert world.bill("3039").review_work == (work,) and not applications(world)
    world.repo.restore(world.repo.dump())
    reviews.advance(TrackingResult(), publish=False)
    assert len(applications(world)) == 1 and len(world.batch.submitted) == 1


@pytest.mark.parametrize("workers", [1, 4])
def test_joint_application_records_the_saved_bill_projection(workers: int) -> None:
    world = joint_world(workers=workers)
    world.run()
    world.batch.resolve()
    world.analysis.collect_batches()
    assert world.bill("1929").joint is None and not applications(world, "1929")

    world.run()
    world.repo.restore(world.repo.dump())
    world.run()

    record = world.bill("1929").joint
    assert record is not None, "the publication consumer stores its comparison on the bill"
    rows = applications(world, "1929")
    assert len(rows) == 1 and rows[0].result_id == record.computation_id
    assert rows[0].target is not None and rows[0].target.kind == "bill"
    assert rows[0].target.ref == "joint" and rows[0].generation is None
    assert len(world.batch.submitted) == 1 and len(world.publisher.joint_bills) == 1


def name_world(*, workers: int = 1) -> World:
    world = World(batch=True, batch_only=True, name_cards=True, workers=workers)
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    world.batch.resolve()
    world.run()
    world.batch.resolve()
    world.analysis.collect_batches()
    assert world.bill("3039").editorial.mnemonic is None and not applications(world)
    return world


@pytest.mark.parametrize("workers", [1, 4])
def test_paid_name_application_records_editorial_selection(workers: int) -> None:
    world = name_world(workers=workers)

    world.run()
    world.repo.restore(world.repo.dump())
    world.run()

    rows = applications(world)
    assert len(rows) == 1 and world.bill("3039").editorial.mnemonic is not None
    assert rows[0].target is not None and rows[0].target.kind == "editorial"
    assert rows[0].target.ref == "mnemonic" and rows[0].generation is None
    assert rows[0].result_id == next(
        row.result_id
        for row in world.repo.list_computation_results(10, "3039")
        if row.purpose == "name"
    )
    assert len(world.batch.submitted) == 2


@pytest.mark.parametrize("kind", ["joint", "name"])
def test_bill_projection_and_secondary_receipt_roll_back_together(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if kind == "joint":
        world = joint_world()
        world.run()
        world.batch.resolve()
        world.analysis.collect_batches()
        number = "1929"
    else:
        world = name_world()
        number = "3039"
    before = world.bill(number)
    save = world.repo.save_result_application

    def crash(application: ResultApplication) -> None:
        if application.target is not None:
            raise RuntimeError("injected bill projection failure")
        save(application)

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, "save_result_application", crash)
        world.run(expect_bugs=True)

    assert world.bill(number).joint == before.joint
    assert world.bill(number).editorial == before.editorial
    assert not applications(world, number)
    world.repo.restore(world.repo.dump())
    world.run()
    assert len(applications(world, number)) == 1


def test_amendment_revision_receipt_names_the_new_publication() -> None:
    world, original = published_world()
    assert original.id is not None, "the fixture stores the original publication"
    world.command("/amendments 1929")
    world.run(track=False)
    world.batch.resolve()
    world.analysis.collect_batches()
    world.repo.restore(world.repo.dump())
    world.analysis.collect_batches()

    revisions = world.repo.list_amendment_revisions(original.id)
    assert len(revisions) == 1 and revisions[0].delivery is not None
    record = StatusChange.model_validate_json(revisions[0].delivery.change_json).amendments
    assert record is not None, "the revision freezes its amendment result"
    rows = applications(world, "1929")
    assert len(rows) == 1 and rows[0].result_id == record.computation_id
    assert rows[0].target is not None and rows[0].target.kind == "publication"
    assert rows[0].target.ref == str(revisions[0].id)
    assert world.repo.publication_by_id(original.id) == original


def test_revision_receipt_failure_preserves_collected_result_and_retryable_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, original = published_world()
    assert original.id is not None, "the fixture stores the original publication"
    world.command("/amendments 1929")
    world.run(track=False)
    world.batch.resolve()

    def crash(application: ResultApplication) -> None:
        raise RuntimeError("injected revision application failure")

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, "save_result_application", crash)
        with pytest.raises(RuntimeError, match="injected revision"):
            world.analysis.collect_batches()

    assert not world.repo.list_amendment_revisions(original.id)
    retained = world.repo.list_computation_results(10, "1929")
    assert len(retained) == 1
    work = world.repo.list_revision_application_work(publication_id=original.id)
    assert len(work) == 1 and work[0].state == "waiting"
    assert not applications(world, "1929") and not world.repo.list_open_llm_batches()
    charged = list(world.analysis.calls)
    world.repo.restore(world.repo.dump())
    world.analysis.collect_batches()
    assert len(world.repo.list_amendment_revisions(original.id)) == 1
    assert len(applications(world, "1929")) == 1 and len(world.batch.submitted) == 1
    assert world.repo.list_computation_results(10, "1929") == retained
    assert world.analysis.calls == charged


@pytest.mark.parametrize("workers", [1, 4])
def test_explanation_applications_name_commands_without_changing_the_bill(workers: int) -> None:
    world = World(workers=workers)
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    before = world.bill("3039")
    world.command("/explain 3039")
    commands_only(world)
    assert not applications(world)
    world.batch.resolve()
    world.command("/explain 3039")
    commands_only(world)
    world.repo.restore(world.repo.dump())
    world.command("/explain 3039")
    commands_only(world)

    rows = applications(world)
    assert len(rows) == 2 and all(
        row.target is not None and row.target.kind == "command" for row in rows
    )
    assert len({row.target.ref for row in rows if row.target is not None}) == 2
    assert rows[0].result_id == rows[1].result_id and world.bill("3039") == before
    assert len(world.batch.submitted) == 1


def test_explanation_receipt_and_executed_marker_commit_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World()
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    world.command("/explain 3039")
    commands_only(world)
    world.batch.resolve()
    world.analysis.collect_batches()
    incoming = world.command("/explain 3039")
    answered = len(world.replier.replies)

    def crash(application: ResultApplication) -> None:
        raise RuntimeError("injected explanation application failure")

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, "save_result_application", crash)
        world.run(
            mode=RunMode.COMMANDS,
            discover=False,
            track=False,
            max_analyze=0,
            max_publish=0,
            expect_bugs=True,
        )

    state = world.repo.command_state(incoming.update_id)
    assert state is not None, "the command was recorded before execution"
    assert state.executed_at is None and not applications(world)
    assert len(world.replier.replies) == answered
    world.repo.restore(world.repo.dump())
    commands_only(world)
    assert not applications(world)
    repeated = world.command("/explain 3039")
    commands_only(world)
    rows = applications(world)
    assert len(rows) == 1 and rows[0].target is not None
    assert rows[0].target.ref == str(repeated.update_id)
    assert len(world.batch.submitted) == 1

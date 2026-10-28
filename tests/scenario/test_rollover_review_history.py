import pytest

from lexinform.models import DocumentReviewRecord, ResultApplication, ResultApplicationTarget
from lexinform.models.reviews import ReviewPurpose
from lexinform.services.analysis import Waiting
from lexinform.services.result_applications import record_secondary_application
from tests.harness import RCL, WYKAZ, World
from tests.scenario.test_commission_reviews import context as commission_context
from tests.scenario.test_document_reviews import FINAL, context
from tests.scenario.test_review_computation_history import submit_review
from tests.scenario.test_rollover_computation_history import Source, rollover


def reviewed_government_world(
    source: Source,
    purpose: ReviewPurpose,
    *,
    source_key: str = "",
    workers: int = 1,
    receipt: bool = True,
) -> tuple[World, str, DocumentReviewRecord]:
    world = World(
        profile="production",
        workers=workers,
        consultation_reviews_enabled=True,
        commission_reviews_enabled=True,
        batch_kinds=frozenset(
            {
                "analysis",
                "reanalysis",
                "consultation_review",
                "commission_review",
                "act_application",
            }
        ),
    )
    if source == "rcl":
        world.add_rcl_project()
        number = RCL
    else:
        world.add_wykaz_entry()
        number = WYKAZ
    world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    selected = (
        commission_context()
        if purpose == "commission_review"
        else context(FINAL, role="final_act")
        if purpose == "act_application"
        else context()
    ).model_copy(update={"number": number})
    world.analysis.start_run()
    assert isinstance(world.analysis.request_document_review(world.bill(number), selected), Waiting)
    intent = world.repo.list_queued_batch_intents()[0]
    submit_review(world, intent)
    world.batch.resolve()
    world.analysis.collect_batches()
    world.analysis.start_run()
    record = world.analysis.request_document_review(world.bill(number), selected)
    assert isinstance(record, DocumentReviewRecord) and record.computation_id is not None, (
        "the fake provider completed the original paid review"
    )
    record = record.model_copy(update={"source_key": source_key})
    with world.repo.atomic():
        world.repo.save_document_review(10, number, record)
        if receipt:
            record_secondary_application(
                world.repo,
                10,
                number,
                record.computation_id,
                target=ResultApplicationTarget(
                    kind="document_review", ref=f"{purpose}/{source_key or selected.memo_key(10)}"
                ),
                now=world.clock.now(),
            )
    return world, number, record


@pytest.mark.parametrize("source", ["rcl", "wykaz"])
@pytest.mark.parametrize("purpose", ["consultation_review", "commission_review", "act_application"])
@pytest.mark.parametrize("cleaned", [False, True])
def test_current_review_receipt_moves_without_rekeying_the_original_paid_result(
    source: Source, purpose: ReviewPurpose, cleaned: bool
) -> None:
    world, number, record = reviewed_government_world(
        source,
        purpose,
        source_key="review-input:original" if cleaned else "",
        workers=4 if cleaned else 1,
    )
    assert record.computation_id is not None, "reviewed_government_world collects a paid result"
    paid = world.repo.get_computation_result(record.computation_id)
    original = world.bill(number)
    old_receipts = world.repo.list_result_applications(10, number)
    requests, messages = list(world.batch.submitted), list(world.publisher.sent)
    if cleaned:
        world.analysis.acknowledge_batch_costs()
        world.clock.advance(days=200)
        world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
        assert not world.repo.load_analysis_memo()
    world.repo.restore(world.repo.dump())

    assert rollover(world).rehomed == 1
    world.repo.restore(world.repo.dump())

    current = world.repo.get(11, number)
    assert current is not None
    assert (
        current.consultation_review,
        current.commission_review,
        current.act_application_review,
    ) == (original.consultation_review, original.commission_review, original.act_application_review)
    assert world.repo.get_computation_result(record.computation_id) == paid
    assert not world.repo.list_computation_results(11, number)
    assert world.repo.list_result_applications(10, number) == old_receipts
    receipts = [row for row in world.repo.list_result_applications(11, number) if row.target]
    prior = [row for row in old_receipts if row.target]
    assert len(receipts) == len(prior) == 1
    assert receipts[0].result_id == prior[0].result_id == record.computation_id
    assert receipts[0].target == prior[0].target
    assert world.batch.submitted == requests and world.publisher.sent == messages
    frozen = world.repo.dump()
    assert rollover(world).rehomed == 0 and world.repo.dump() == frozen


def test_secondary_receipt_failure_rolls_back_the_move_and_primary_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, number, _ = reviewed_government_world("rcl", "consultation_review")
    frozen = world.repo.dump()
    save = world.repo.save_result_application

    def refuse_secondary(application: ResultApplication) -> None:
        if application.target is not None:
            raise RuntimeError("injected secondary rollover failure")
        save(application)

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, "save_result_application", refuse_secondary)
        with pytest.raises(RuntimeError, match="secondary rollover failure"):
            rollover(world)

    assert world.repo.dump() == frozen
    assert world.repo.get(10, number) is not None and world.repo.get(11, number) is None
    assert rollover(world).rehomed == 1


@pytest.mark.parametrize("gap", ["unknown_result", "different_target", "no_receipt"])
def test_rollover_does_not_infer_missing_or_unrelated_review_history(gap: str) -> None:
    world, number, record = reviewed_government_world(
        "rcl", "consultation_review", receipt=gap != "no_receipt"
    )
    replacement = record.model_copy(
        update={
            "computation_id": None if gap == "unknown_result" else record.computation_id,
            "source_key": "review-input:different"
            if gap == "different_target"
            else record.source_key,
        }
    )
    world.repo.save_document_review(10, number, replacement)
    assert rollover(world).rehomed == 1

    assert not any(row.target for row in world.repo.list_result_applications(11, number))

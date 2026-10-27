import pytest

from lexinform.models import BillStatus, ReviewInputReference, ReviewPurpose
from lexinform.services.analysis import Waiting
from tests.harness import RCL, World, summary
from tests.scenario.test_commission_reviews import context as commission_context
from tests.scenario.test_document_reviews import context
from tests.scenario.test_secondary_computation_history import KINDS, Kind, ask, prepared_world


@pytest.mark.parametrize("purpose", ["consultation_review", "commission_review"])
@pytest.mark.parametrize("submitted", [False, True])
@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
def test_primary_handoff_keeps_a_paid_review_on_its_frozen_subject(
    purpose: ReviewPurpose, submitted: bool, workers: int, model: str
) -> None:
    world = World(
        profile="production",
        workers=workers,
        analysis_model=model,
        batch_kinds=frozenset(
            {"analysis", "reanalysis", "consultation_review", "commission_review"}
        ),
        consultation_reviews_enabled=True,
        commission_reviews_enabled=True,
    )
    project = world.add_rcl_project()
    world.run()
    source = (commission_context() if purpose == "commission_review" else context()).model_copy(
        update={"number": RCL, "title": project.title}
    )
    assert isinstance(world.analysis.request_document_review(world.bill(RCL), source), Waiting)
    intent = world.repo.list_queued_batch_intents()[0]
    if submitted:
        world.analysis.submit_queued_batches()
        assert len(world.batch.submitted) == 2
    world.repo.upsert_summary(summary("5", project.title), now=world.clock.now())
    world.repo.set_status(10, "5", BillStatus.BATCH_PENDING)

    with world.repo.atomic():
        world.repo.move_primary_analysis_work(10, RCL, "5")
        world.repo.link_bills(10, RCL, "5")
    if not submitted:
        assert world.repo.list_queued_batch_intents() == [intent]
        world.analysis.submit_queued_batches()
    world.repo.restore(world.repo.dump())
    world.batch.resolve()
    world.analysis.collect_batches()

    reviews = [
        row for row in world.repo.list_computation_results(10, RCL) if row.purpose == purpose
    ]
    assert len(reviews) == 1 and reviews[0].review_input == ReviewInputReference.from_context(
        source, term=10
    )
    assert reviews[0].request_sha256 == intent.request.payload_sha256
    target = world.repo.get(10, "5")
    assert target is not None and target.ready_analysis is not None
    assert not [
        row for row in world.repo.list_computation_results(10, "5") if row.purpose == purpose
    ]


@pytest.mark.parametrize(
    ("kind", "submitted"),
    [
        (kind, submitted)
        for kind in KINDS
        for submitted in (False, True)
        if kind != "explanation" or submitted
    ],
)
@pytest.mark.parametrize("workers", [1, 4])
def test_primary_handoff_preserves_other_secondary_work(
    kind: Kind, submitted: bool, workers: int
) -> None:
    world = prepared_world(workers=workers)
    history = world.repo.list_computation_results(10, "3039")
    assert isinstance(ask(world, kind), Waiting)
    intents = world.repo.list_queued_batch_intents()
    assert bool(intents) == (kind != "explanation")
    request = intents[0].request if intents else world.batch.submitted[-1]
    if submitted:
        world.analysis.submit_queued_batches()
    world.repo.upsert_summary(summary("5", "Projekt ustawy o cudzoziemcach"), now=world.clock.now())

    with world.repo.atomic():
        world.repo.move_primary_analysis_work(10, "3039", "5")
        world.repo.link_bills(10, "3039", "5")
    if not submitted:
        assert world.repo.list_queued_batch_intents() == intents
        world.analysis.submit_queued_batches()
    world.repo.restore(world.repo.dump())
    world.batch.resolve()
    world.analysis.collect_batches()

    results = world.repo.list_computation_results(10, "3039")
    assert results[: len(history)] == history
    secondary = [result for result in results if result.purpose == kind]
    assert len(secondary) == 1
    assert secondary[0].custom_id == request.custom_id
    assert secondary[0].request_sha256 == request.payload_sha256
    assert not world.repo.list_computation_results(10, "5")

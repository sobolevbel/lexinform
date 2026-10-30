import pytest

from lexinform.models import Bill, DocumentReviewRecord, ReviewContext, ReviewPurpose, ReviewSource
from lexinform.services.tracking_result import TrackingResult
from tests.harness import TERM, World
from tests.scenario.test_document_reviews import FINAL, context, finding, review


def test_consultation_work_retains_comparison_across_restore_and_later_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(
        batch=True,
        batch_kinds=frozenset({"consultation_review"}),
        consultation_reviews_enabled=True,
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    comparison = (
        context(FINAL, role="comparison_text")
        .documents[0]
        .model_copy(
            update={"identity": "comparison/retained", "url": "https://source.test/retained.pdf"}
        )
    )
    previous = DocumentReviewRecord(
        review=review(finding()),
        context=context().model_copy(update={"documents": (*context().documents, comparison)}),
        created_at=world.clock.now(),
    )
    world.repo.save_document_review(TERM, "3039", previous)
    service = world.container.document_reviews(dry_run=False)
    service.request(world.bill("3039"), "consultation_review", TrackingResult())
    (work,) = world.bill("3039").review_work
    assert work.comparison_document == comparison
    assert Bill.model_validate_json(work.input_bill_json).consultation_review is None
    service.request(world.bill("3039"), "consultation_review", TrackingResult())
    assert world.bill("3039").review_work == (work,)
    world.repo.restore(world.repo.dump())
    world.repo.save_document_review(
        TERM,
        "3039",
        previous.model_copy(update={"context": context("Pkt 1. Nowa odpowiedź.")}),
    )
    seen: list[tuple[ReviewSource, ...]] = []

    def capture(
        bill: Bill, purpose: ReviewPurpose, sources: tuple[ReviewSource, ...]
    ) -> ReviewContext | None:
        assert bill.consultation_review is None and purpose == "consultation_review"
        seen.append(sources)
        return None

    monkeypatch.setattr(world.analysis, "review_context", capture)
    before = tuple(world.gateway.calls)
    service.advance(TrackingResult(), publish=False)
    (sources,) = seen
    (saved,) = [source for source in sources if source.role == "comparison_text"]
    assert saved.snapshot == comparison and saved.identity == comparison.identity
    assert tuple(world.gateway.calls) == before
    assert world.bill("3039").review_work[0].comparison_document == comparison


def test_consultation_source_identity_binds_the_retained_comparison() -> None:
    world = World(
        batch=True,
        batch_kinds=frozenset({"consultation_review"}),
        consultation_reviews_enabled=True,
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    service = world.container.document_reviews(dry_run=False)
    for text in (FINAL, FINAL.replace("nie składają", "składają")):
        comparison = context(text, role="comparison_text").documents[0]
        previous = DocumentReviewRecord(
            review=review(finding()),
            context=context().model_copy(update={"documents": (*context().documents, comparison)}),
            created_at=world.clock.now(),
        )
        world.repo.save_document_review(TERM, "3039", previous)
        service.request(world.bill("3039"), "consultation_review", TrackingResult())
    works = world.bill("3039").review_work
    assert len(works) == 2
    assert works[0].memo_key != works[1].memo_key
    assert works[0].source_key != works[1].source_key
    assert works[0].comparison_document != works[1].comparison_document


def test_unchanged_legacy_consultation_review_does_not_request_a_new_batch() -> None:
    world = World(
        batch=True,
        batch_kinds=frozenset({"consultation_review"}),
        consultation_reviews_enabled=True,
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    service = world.container.document_reviews(dry_run=False)
    service.request(world.bill("3039"), "consultation_review", TrackingResult())
    (initial,) = world.bill("3039").review_work
    comparison = context(FINAL, role="comparison_text").documents[0]
    previous = DocumentReviewRecord(
        review=review(finding()),
        context=context().model_copy(update={"documents": (*context().documents, comparison)}),
        created_at=world.clock.now(),
        source_key="review-input:" + "f" * 64,
    )
    world.repo.save_document_review(TERM, "3039", previous)
    service.request(world.bill("3039"), "consultation_review", TrackingResult(), force_check=True)
    assert world.bill("3039").review_work == (initial,)
    assert initial.comparison_document is None
    world.repo.save_review_work(TERM, "3039", ())
    world.repo.save_document_review(
        TERM, "3039", previous.model_copy(update={"source_key": initial.source_key})
    )
    service.request(world.bill("3039"), "consultation_review", TrackingResult())
    assert not world.bill("3039").review_work
    service.request(world.bill("3039"), "consultation_review", TrackingResult(), force_check=True)
    (forced,) = world.bill("3039").review_work
    assert forced.comparison_document == comparison and forced.source_key != initial.source_key
    assert not world.batch.submitted

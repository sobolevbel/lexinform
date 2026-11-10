import datetime as dt

import pytest

from lexinform.models import (
    BatchRequest,
    DeliveryPlan,
    DocumentReview,
    DocumentReviewRecord,
    Publication,
    PublicationKind,
    PublicationStatus,
    ReviewWork,
    validate_review,
)
from lexinform.services.analysis import SECONDARY_BATCH_SUBMISSIONS, Waiting
from lexinform.services.tracking_result import TrackingResult
from tests.document_review_cases import FINAL, context, finding, review, rule
from tests.harness import World, act


@pytest.mark.parametrize("workers", [1, 4])
def test_review_batch_survives_restore_unknown_submission_and_preserves_bill_status(
    workers: int,
) -> None:
    w = World(
        batch=True,
        workers=workers,
        batch_kinds=frozenset({"consultation_review"}),
        consultation_reviews_enabled=True,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    before = w.bill("3039").status
    ctx = context()
    w.batch.secondary_script[("3039", "consultation_review")] = review(finding())
    assert isinstance(w.analysis.request_document_review(w.bill("3039"), ctx), Waiting)
    intent = w.repo.list_queued_batch_intents()[0]
    w.repo.mark_batch_intents_submitting([intent.request.custom_id])
    w.clock.advance(hours=48)
    w.repo.restore(w.repo.dump())
    w.analysis.start_run()
    assert isinstance(w.analysis.request_document_review(w.bill("3039"), ctx), Waiting)
    assert not w.batch.submitted
    w.repo.mark_batch_intents_queued([intent.request.custom_id])
    w.analysis.submit_queued_batches()
    w.batch.resolve()
    w.analysis.collect_batches()
    w.repo.restore(w.repo.dump())
    w.analysis.start_run()
    result = w.analysis.request_document_review(w.bill("3039"), ctx)
    assert isinstance(result, DocumentReviewRecord)
    assert result.input_tokens == 0
    assert result.review.consultation_findings[0].outcome == "accepted"
    assert result.context.documents[0].sha256 == ctx.documents[0].sha256
    assert w.bill("3039").status == before
    assert len(w.batch.submitted) == 1
    assert not w.llm.joint_contexts


def test_legacy_act_review_work_is_preserved_without_new_analysis_or_posts() -> None:
    w = World(batch=True, batch_kinds=frozenset({"act_application"}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.repo.save_act(10, "3039", act())
    ctx = context(FINAL, role="final_act")
    work = ReviewWork(
        memo_key=ctx.memo_key(10),
        purpose="act_application",
        context=ctx,
        since=w.clock.now(),
        baseline_day=dt.date(2026, 9, 10),
    )
    w.repo.save_review_work(10, "3039", (work,))
    submitted = list(w.batch.submitted)
    w.clock.advance(days=30)
    w.repo.restore(w.repo.dump())
    result = TrackingResult()
    w.container.document_reviews(dry_run=False).advance(result, publish=True)

    assert w.bill("3039").review_work == (work,)
    assert w.batch.submitted == submitted
    assert not w.repo.list_queued_batch_intents()
    assert result.act_reviews_posted == result.effective_rule_messages == 0
    assert not w.publisher.texts(PublicationKind.ACT_APPLICATION)
    assert not w.publisher.texts(PublicationKind.EFFECTIVE_RULES)


@pytest.mark.parametrize(("limit", "estimate"), [(0.01, 0.1), (2.0, 4.4416)])
def test_review_cost_guard_never_creates_a_synchronous_fallback(
    monkeypatch: pytest.MonkeyPatch, limit: float, estimate: float
) -> None:
    w = World(
        batch=True,
        batch_kinds=frozenset({"consultation_review"}),
        max_bill_cost_usd=limit,
        max_run_cost_usd=15.0,
        consultation_reviews_enabled=True,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.analysis.start_run()
    prepare = w.batch.prepare_request

    def prepare_review(request: BatchRequest) -> BatchRequest:
        return prepare(request).model_copy(update={"estimated_cost_usd": estimate})

    monkeypatch.setattr(w.batch, "prepare_request", prepare_review)
    bill = w.bill("3039")
    before = bill.status
    assert isinstance(w.analysis.request_document_review(bill, context()), Waiting)
    assert not w.repo.list_queued_batch_intents()
    assert w.analysis.stopped == (
        f"per-bill cost limit exceeded for 3039 (request ≈${estimate:.2f} > ${limit:.2f}); "
        "document review remains pending"
    )
    assert w.bill("3039").status == before


def test_review_reports_the_run_limit_when_reservation_fails() -> None:
    w = World(
        batch=True,
        max_bill_cost_usd=2.0,
        max_run_cost_usd=0.01,
        consultation_reviews_enabled=True,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.analysis.start_run()

    assert isinstance(w.analysis.request_document_review(w.bill("3039"), context()), Waiting)

    assert not w.repo.list_queued_batch_intents()
    assert w.analysis.stopped == (
        "run cost limit reached (spent/reserved ≈$0.00, next request exceeds $0.01); "
        "document review waits for the next run"
    )


def test_legacy_queued_act_analysis_is_not_submitted() -> None:
    w = World(batch=True, batch_kinds=frozenset({"act_application"}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.analysis.start_run()
    assert isinstance(
        w.analysis.request_document_review(w.bill("3039"), context(FINAL, role="final_act")),
        Waiting,
    )
    pending = w.repo.list_queued_batch_intents()
    assert pending
    submitted = list(w.batch.submitted)

    w.analysis.submit_queued_batches()

    assert w.repo.list_queued_batch_intents() == pending
    assert w.batch.submitted == submitted


@pytest.mark.parametrize("kind", [PublicationKind.ACT_APPLICATION, PublicationKind.EFFECTIVE_RULES])
@pytest.mark.parametrize(
    "status", [PublicationStatus.QUEUED, PublicationStatus.FAILED, PublicationStatus.UNKNOWN]
)
def test_legacy_act_review_deliveries_are_stopped_without_changing_payloads(
    kind: PublicationKind,
    status: PublicationStatus,
) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.repo.save_act(10, "3039", act())
    record = DocumentReviewRecord(
        review=DocumentReview(
            purpose="act_application",
            complete=True,
            summary="",
            consultation_findings=(),
            effective_rules=(rule(),),
            unresolved=(),
        ),
        context=context(FINAL, role="final_act"),
        created_at=w.clock.now(),
    )
    w.repo.save_document_review(10, "3039", record)
    poster = w.container.poster(dry_run=False)
    pub = poster.prepare_message(w.bill("3039"), kind, ref="legacy")
    assert pub.id is not None
    w.repo.mark_publication(pub.id, status, count_attempt=False)
    saved = w.repo.publication_by_id(pub.id)
    assert saved is not None

    assert not poster.deliver(saved)
    current = w.repo.publication_by_id(pub.id)
    assert current is not None and current.delivery == saved.delivery
    assert current.status is (
        PublicationStatus.UNKNOWN
        if status is PublicationStatus.UNKNOWN
        else PublicationStatus.SKIPPED
    )
    assert w.bill("3039").act_application_review == record
    assert not w.publisher.texts(kind)


@pytest.mark.parametrize("kind", [PublicationKind.ACT_APPLICATION, PublicationKind.EFFECTIVE_RULES])
def test_legacy_act_review_continuation_is_not_sent_after_confirmed_root(
    kind: PublicationKind,
) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    poster = w.container.poster(dry_run=False)
    root_id = poster.record(w.bill("3039"), kind, PublicationStatus.SENT, ref="legacy")
    child_id = w.repo.create_publication(
        Publication(
            term=10,
            number="3039",
            channel_id="@test",
            kind=PublicationKind.MESSAGE_PART,
            status=PublicationStatus.QUEUED,
            ref="legacy/2",
            created_at=w.clock.now(),
            delivery=DeliveryPlan(
                bill_json=w.bill("3039").model_dump_json(),
                rendered_text="Продолжение разбора",
                continuation_root=root_id,
                continuation_parent=root_id,
                continuation_index=2,
            ),
        )
    )
    child = w.repo.publication_by_id(child_id)
    assert child is not None

    assert not poster.deliver(child)
    saved = w.repo.publication_by_id(child_id)
    assert saved is not None and saved.status is PublicationStatus.SKIPPED
    assert saved.delivery == child.delivery


def test_retained_review_reuses_content_even_after_generic_memo_retention() -> None:
    w = World(
        batch=True,
        batch_kinds=frozenset({"consultation_review"}),
        consultation_reviews_enabled=True,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    ctx = context()
    record = DocumentReviewRecord(
        review=validate_review(review(finding()), ctx),
        context=ctx,
        created_at=w.clock.now(),
        input_tokens=123,
    )
    w.repo.save_document_review(10, "3039", record)
    w.repo.restore(w.repo.dump())
    answer = w.analysis.request_document_review(w.bill("3039"), ctx)
    assert isinstance(answer, DocumentReviewRecord) and answer.input_tokens == 0
    assert not w.repo.list_queued_batch_intents()


def test_review_context_cannot_cross_bill_identity() -> None:
    w = World(
        batch=True,
        batch_kinds=frozenset({"consultation_review"}),
        consultation_reviews_enabled=True,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    with pytest.raises(ValueError, match="different bill"):
        w.analysis.request_document_review(
            w.bill("3039"), context().model_copy(update={"number": "3040"})
        )
    assert not w.repo.list_queued_batch_intents()


def test_primary_act_notices_continue_without_secondary_act_review() -> None:
    w = World(batch=True, batch_kinds=frozenset({"act_application"}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.publish_act("3039")
    today = w.clock.now().date()
    published = act(
        promulgation_date=today,
        entry_into_force=today + dt.timedelta(days=1),
        fetched_at=w.clock.now(),
    )
    w.gateway.acts[published.eli] = published

    first = w.run()
    assert first.acts_published == 1 and first.act_reviews_posted == 0
    assert not w.bill("3039").review_work
    w.repo.save_document_review(
        10,
        "3039",
        DocumentReviewRecord(
            review=DocumentReview(
                purpose="act_application",
                complete=True,
                summary="",
                consultation_findings=(),
                effective_rules=(rule(),),
                unresolved=(),
            ),
            context=context(FINAL, role="final_act"),
            created_at=w.clock.now(),
        ),
    )
    w.clock.advance(days=1)
    second = w.run()
    assert second.in_force_posted == 1 and second.act_reviews_posted == 0
    assert "Как применяется окончательный акт" not in w.publisher.texts(PublicationKind.IN_FORCE)[0]
    assert rule().change not in w.publisher.texts(PublicationKind.IN_FORCE)[0]
    assert not w.bill("3039").review_work
    assert not w.publisher.texts(PublicationKind.ACT_APPLICATION)


def test_a_review_failed_at_every_submission_keeps_no_documents_and_is_not_retried() -> None:
    w = World(
        batch=True,
        batch_kinds=frozenset({"consultation_review"}),
        consultation_reviews_enabled=True,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    ctx = context()
    work = ReviewWork(
        memo_key=ctx.memo_key(10),
        purpose="consultation_review",
        context=ctx,
        source_key="review-input:3039",
        since=w.clock.now(),
        baseline_day=dt.date(2026, 9, 10),
    )
    w.repo.save_review_work(10, "3039", (work,))
    w.batch.secondary_script[("3039", "consultation_review")] = RuntimeError(
        "response is incomplete"
    )
    reviews = w.container.document_reviews(dry_run=False)
    for _ in range(SECONDARY_BATCH_SUBMISSIONS):
        w.analysis.start_run()
        reviews.advance(TrackingResult(), publish=True)
        w.analysis.submit_queued_batches()
        w.batch.resolve()
        w.analysis.collect_batches()
    submitted = len(w.batch.submitted)

    result = TrackingResult()
    w.analysis.start_run()
    reviews.advance(result, publish=True)
    reviews.advance(result, publish=True)

    (left,) = w.bill("3039").review_work
    assert left.abandoned and left.source_key == work.source_key
    assert submitted == SECONDARY_BATCH_SUBMISSIONS == len(w.batch.submitted)
    assert not result.partial_errors

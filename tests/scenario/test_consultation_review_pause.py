from dataclasses import replace

import pytest

from lexinform.container import Container
from lexinform.models import (
    DeliveryPlan,
    DocumentReviewRecord,
    Publication,
    PublicationKind,
    PublicationStatus,
    ReviewWork,
)
from lexinform.services.analysis import Waiting
from lexinform.services.pipeline import RunOptions
from lexinform.services.tracking_result import TrackingResult
from lexinform.settings import Settings
from tests.harness import TERM, World
from tests.scenario.test_document_reviews import context, finding, review


def followed_world(*, max_run_cost_usd: float = 0.0) -> World:
    w = World(
        batch=True,
        batch_kinds=frozenset({"analysis", "consultation_review"}),
        consultation_reviews_enabled=True,
        max_run_cost_usd=max_run_cost_usd,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.batch.resolve()
    w.run()
    return w


def paused_container(w: World) -> Container:
    return replace(
        w.container,
        settings=w.container.settings.model_copy(update={"consultation_reviews_enabled": False}),
        analysis_options_override=None,
    )


def test_consultation_reviews_are_off_by_default_even_in_batch_kinds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LEXINFORM_CONSULTATION_REVIEWS_ENABLED", raising=False)
    monkeypatch.setenv("LEXINFORM_LLM_BATCH_KINDS", "analysis,consultation_review")
    settings = Settings(_env_file=None)
    assert not settings.consultation_reviews_enabled
    monkeypatch.setenv("LEXINFORM_CONSULTATION_REVIEWS_ENABLED", "true")
    assert Settings(_env_file=None).consultation_reviews_enabled
    w = World(consultation_reviews_enabled=False)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    result = TrackingResult()

    w.container.document_reviews(dry_run=False).request(
        w.bill("3039"), "consultation_review", result
    )
    assert isinstance(w.analysis.request_document_review(w.bill("3039"), context()), Waiting)

    assert not w.bill("3039").review_work
    assert not w.repo.list_queued_batch_intents()
    assert len(w.publisher.texts(PublicationKind.NEW_BILL)) == 1


@pytest.mark.parametrize("uncertain", [False, True])
def test_paused_intents_stay_saved_and_only_uncertain_submission_reserves_budget(
    uncertain: bool,
) -> None:
    w = followed_world(max_run_cost_usd=0.15)
    w.analysis.start_run()
    ctx = context()
    work = ReviewWork(
        memo_key=ctx.memo_key(TERM),
        purpose="consultation_review",
        context=ctx,
        since=w.clock.now(),
        baseline_day=w.clock.now().date(),
    )
    w.repo.save_review_work(TERM, "3039", (work,))
    assert isinstance(w.analysis.request_document_review(w.bill("3039"), ctx), Waiting)
    intents = w.repo.list_queued_batch_intents()
    assert len(intents) == 1
    if uncertain:
        w.repo.mark_batch_intents_submitting([intents[0].request.custom_id])
        intents = w.repo.list_submitting_batch_intents()
    w.repo.restore(w.repo.dump())
    w.add_bill("3040", "Projekt ustawy o cudzoziemcach")
    paused = paused_container(w)

    report = paused.pipeline(dry_run=False).run(RunOptions(term=TERM))

    assert w.bill("3039").review_work == (work,)
    pending = (
        w.repo.list_submitting_batch_intents() if uncertain else w.repo.list_queued_batch_intents()
    )
    assert pending == intents
    assert not any(req.question.kind == "consultation_review" for req in w.batch.submitted)
    assert any(req.number == "3040" for req in w.batch.submitted) is not uncertain
    assert not report.consultation_reviews_posted


def test_inflight_review_is_collected_and_accounted_while_paused_then_reused() -> None:
    w = followed_world()
    w.analysis.start_run()
    ctx = context()
    work = ReviewWork(
        memo_key=ctx.memo_key(TERM),
        purpose="consultation_review",
        context=ctx,
        since=w.clock.now(),
        baseline_day=w.clock.now().date(),
    )
    w.repo.save_review_work(TERM, "3039", (work,))
    w.batch.secondary_script[("3039", "consultation_review")] = review(finding())
    assert isinstance(w.analysis.request_document_review(w.bill("3039"), ctx), Waiting)
    w.analysis.submit_queued_batches()
    submitted = list(w.batch.submitted)
    w.batch.resolve()
    paused = paused_container(w)

    report = paused.pipeline(dry_run=False).run(RunOptions(term=TERM))

    assert not report.errors
    assert any(call.kind == "consultation_review" for call in report.llm_calls)
    assert ctx.memo_key(TERM) in w.repo.load_analysis_memo()
    assert w.bill("3039").review_work == (work,)
    assert w.bill("3039").consultation_review is None
    assert not w.repo.list_unaccounted_batch_items()
    assert w.batch.submitted == submitted
    resumed = replace(
        w.container, settings=w.container.settings.model_copy(), analysis_options_override=None
    )
    resumed.pipeline(dry_run=False).run(RunOptions(term=TERM))
    assert w.bill("3039").consultation_review is not None
    assert not w.bill("3039").review_work
    assert w.batch.submitted == submitted
    assert len(w.publisher.texts(PublicationKind.CONSULTATION_REVIEW)) == 1


@pytest.mark.parametrize(
    "status", [PublicationStatus.QUEUED, PublicationStatus.FAILED, PublicationStatus.UNKNOWN]
)
@pytest.mark.parametrize("continuation", [False, True])
def test_paused_review_delivery_and_parts_keep_payload_status_and_attempts(
    status: PublicationStatus, continuation: bool
) -> None:
    w = followed_world()
    ctx = context()
    record = DocumentReviewRecord(context=ctx, review=review(finding()), created_at=w.clock.now())
    w.repo.save_document_review(TERM, "3039", record)
    poster = w.container.poster(dry_run=False)
    root = poster.prepare_message(w.bill("3039"), PublicationKind.CONSULTATION_REVIEW)
    assert root.id is not None
    pub_id = root.id
    if continuation:
        w.repo.mark_publication(root.id, PublicationStatus.SENT, count_attempt=False)
        pub_id = w.repo.create_publication(
            Publication(
                term=TERM,
                number="3039",
                channel_id="@test",
                kind=PublicationKind.MESSAGE_PART,
                status=status,
                created_at=w.clock.now(),
                ref="paused/2",
                delivery=DeliveryPlan(
                    bill_json=w.bill("3039").model_dump_json(),
                    rendered_text="Продолжение разбора",
                    continuation_root=root.id,
                    continuation_parent=root.id,
                ),
            )
        )
    w.repo.mark_publication(pub_id, status, count_attempt=True)
    saved = w.repo.publication_by_id(pub_id)
    assert saved is not None
    paused = paused_container(w)

    result = paused.delivery_service(dry_run=False).run()
    tracked = TrackingResult()
    paused.document_reviews(dry_run=False).advance(tracked, publish=True)

    assert not result.errors
    assert not tracked.failed and not tracked.partial_errors
    assert not paused.poster(dry_run=False).deliver(saved)
    assert w.repo.publication_by_id(pub_id) == saved
    assert w.bill("3039").consultation_review == record
    assert not w.publisher.texts(PublicationKind.CONSULTATION_REVIEW)
    assert not w.publisher.texts(PublicationKind.MESSAGE_PART)

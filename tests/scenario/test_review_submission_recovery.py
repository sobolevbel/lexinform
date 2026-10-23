import datetime as dt
import sqlite3
from dataclasses import replace

import pytest

from lexinform.errors import LlmContextExceededError
from lexinform.models import (
    DocumentReview,
    DocumentReviewRecord,
    ReviewContext,
    ReviewWork,
    validate_review,
)
from lexinform.services.tracking_result import TrackingResult
from tests.fakes import FakeBatchBackend
from tests.harness import TERM, World
from tests.scenario.test_document_reviews import context, finding, review


def ready(*, overflow: FakeBatchBackend | None = None) -> tuple[World, ReviewContext]:
    world = World(
        profile="production",
        analysis_model="gpt-5.1",
        overflow_batch=overflow,
        consultation_reviews_enabled=True,
        batch_kinds=frozenset({"analysis", "consultation_review"}),
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    world.batch.resolve()
    world.run()
    ctx = context()
    world.repo.save_review_work(
        TERM,
        "3039",
        (
            ReviewWork(
                memo_key=ctx.memo_key(TERM),
                purpose="consultation_review",
                context=ctx,
                source_key="review-input:3039",
                since=world.clock.now(),
                baseline_day=dt.date(2026, 9, 10),
            ),
        ),
    )
    return world, ctx


def resolve_review(
    world: World, outcome: DocumentReview | Exception, *, backend: FakeBatchBackend | None = None
) -> None:
    writer = backend or world.batch
    writer.secondary_script[("3039", "consultation_review")] = outcome
    world.analysis.start_run()
    world.container.document_reviews(dry_run=False).advance(TrackingResult(), publish=False)
    world.analysis.submit_queued_batches()
    writer.resolve()
    world.analysis.collect_batches()


def payloads(world: World) -> dict[str, int]:
    connection = sqlite3.connect(":memory:")
    connection.executescript(world.repo.dump())
    row = connection.execute(
        "SELECT COUNT(*),"
        " COALESCE(SUM(json_type(meta_json,'$.review_context') = 'object'),0),"
        " COALESCE(SUM(json_type(result_json,'$.answer') = 'object'),0),"
        " COALESCE(SUM(json_extract(result_json,'$.context_exceeded') = 1),0)"
        " FROM llm_batch_items WHERE call_kind='consultation_review'"
    ).fetchone()
    assert row is not None, "aggregate payload counts always return one row"
    result = {
        "items": int(row[0]),
        "contexts": int(row[1]),
        "answers": int(row[2]),
        "context_rejections": int(row[3]),
    }
    connection.close()
    return result


def cleanup(world: World, *, accounted: bool) -> None:
    world.analysis.start_run()
    if accounted:
        world.analysis.acknowledge_batch_costs()
    world.analysis.collect_batches()
    world.repo.prune_history(
        before=world.clock.now() + dt.timedelta(days=1),
        memo_before=world.clock.now() + dt.timedelta(days=181),
    )


def restart(world: World) -> None:
    world.repo.restore(world.repo.dump())
    world.container = replace(world.container)
    world.analysis = world.container.analysis_service()
    world.analysis.start_run()


@pytest.mark.parametrize("accounted", [False, True])
@pytest.mark.parametrize("restored", [False, True])
def test_successful_second_submission_is_applied_from_memo_before_exhaustion(
    accounted: bool, restored: bool
) -> None:
    world, ctx = ready()
    resolve_review(world, RuntimeError("first submission failed"))
    resolve_review(world, review(finding()))
    job = world.repo.batch_job(ctx.memo_key(TERM))
    assert job is not None and job.state == "failed" and job.submissions == 2, (
        "batch_job counts consumed results; the validated review memo decides reuse"
    )
    cleanup(world, accounted=accounted)
    stored = payloads(world)
    assert stored == {
        "items": 2,
        "contexts": 0 if accounted else 2,
        "answers": 0 if accounted else 1,
        "context_rejections": 0,
    }
    assert ctx.memo_key(TERM) in world.repo.load_analysis_memo(), (
        "pending review work protects the validated memo through retention"
    )
    if not accounted:
        assert len(world.repo.list_unaccounted_batch_items()) == 2
    if restored:
        restart(world)
    submitted = len(world.batch.submitted)
    result = TrackingResult()

    world.container.document_reviews(dry_run=False).advance(result, publish=False)

    bill = world.bill("3039")
    assert bill.consultation_review is not None, "the validated second result must be applied"
    assert bill.consultation_review.review.consultation_findings[0].outcome == "accepted"
    assert bill.consultation_review.context == ctx
    assert not bill.review_work and not result.partial_errors
    assert len(world.batch.submitted) == submitted
    assert not world.repo.list_queued_batch_intents()


def test_two_failed_submissions_still_abandon_only_after_allowed_attempts() -> None:
    world, ctx = ready()
    for _ in range(2):
        resolve_review(world, RuntimeError("submission failed"))
    cleanup(world, accounted=True)
    assert payloads(world)["contexts"] == 0
    assert ctx.memo_key(TERM) not in world.repo.load_analysis_memo()
    restart(world)
    submitted = len(world.batch.submitted)

    world.container.document_reviews(dry_run=False).advance(TrackingResult(), publish=False)

    (work,) = world.bill("3039").review_work
    assert work.abandoned and work.source_key == "review-input:3039"
    assert work.context is None and not work.input_bill_json
    assert len(world.batch.submitted) == submitted
    assert not world.repo.list_queued_batch_intents()


def test_same_context_retained_review_wins_over_exhausted_batch_history_without_memo() -> None:
    world, ctx = ready()
    for _ in range(2):
        resolve_review(world, RuntimeError("historical submission failed"))
    assert ctx.memo_key(TERM) not in world.repo.load_analysis_memo()
    world.repo.save_document_review(
        TERM,
        "3039",
        DocumentReviewRecord(
            review=validate_review(review(finding()), ctx),
            context=ctx,
            created_at=world.clock.now(),
        ),
    )
    submitted = len(world.batch.submitted)
    result = TrackingResult()

    world.container.document_reviews(dry_run=False).advance(result, publish=False)

    assert not world.bill("3039").review_work
    assert ctx.memo_key(TERM) in world.repo.load_analysis_memo()
    assert world.bill("3039").consultation_review is not None
    assert not result.partial_errors and len(world.batch.submitted) == submitted


def test_primary_context_refusal_survives_cleanup_and_restart_while_overflow_waits() -> None:
    overflow = FakeBatchBackend(id_prefix="overflow")
    world, ctx = ready(overflow=overflow)
    resolve_review(world, LlmContextExceededError("primary context overflow"))
    cleanup(world, accounted=True)
    assert payloads(world) == {"items": 1, "contexts": 0, "answers": 0, "context_rejections": 1}
    assert ctx.memo_key(TERM) in world.repo.context_rejected_batch_ids()
    restart(world)
    reviews = world.container.document_reviews(dry_run=False)
    reviews.advance(TrackingResult(), publish=False)
    (intent,) = world.repo.list_queued_batch_intents()
    assert intent.request.custom_id == "overflow:" + ctx.memo_key(TERM)
    assert intent.provider == "anthropic"
    assert world.bill("3039").review_work[0].context == ctx
    cleanup(world, accounted=True)
    restart(world)
    reviews = world.container.document_reviews(dry_run=False)
    reviews.advance(TrackingResult(), publish=False)
    (queued,) = world.repo.list_queued_batch_intents()
    assert queued == intent
    assert not world.bill("3039").review_work[0].abandoned
    assert payloads(world)["context_rejections"] == 1

    resolve_review(world, RuntimeError("first overflow submission failed"), backend=overflow)
    resolve_review(world, review(finding()), backend=overflow)
    cleanup(world, accounted=True)
    restart(world)
    submitted = len(overflow.submitted)
    result = TrackingResult()

    world.container.document_reviews(dry_run=False).advance(result, publish=False)

    assert world.bill("3039").consultation_review is not None
    assert not world.bill("3039").review_work and not result.partial_errors
    assert submitted == 2 and len(overflow.submitted) == submitted
    assert payloads(world)["context_rejections"] == 1

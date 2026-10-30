import hashlib

import pytest

from lexinform.document_fingerprints import text_digest
from lexinform.models import (
    BatchJob,
    BatchRequest,
    ComputationResult,
    DocumentReviewRecord,
    ReviewContext,
    ReviewSource,
    TextDocument,
)
from lexinform.models.reviews import ReviewPurpose
from lexinform.services.analysis import Waiting
from tests.harness import World
from tests.scenario.test_commission_reviews import context as commission_context
from tests.scenario.test_review_computation_history import PURPOSES, queued_review, submit_review


def replaced_review(
    purpose: ReviewPurpose, *, missing_optional: bool = False
) -> tuple[World, ReviewContext, ComputationResult, DocumentReviewRecord]:
    if missing_optional:
        world = World(
            batch=True,
            batch_kinds=frozenset({"commission_review"}),
            commission_reviews_enabled=True,
        )
        world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
        world.run(track=False)
        world.analysis.start_run()
        documents = commission_context().documents
        missing = TextDocument(url="https://api.test/optional-comparison.pdf", kind="print")
        world.gateway.files[missing.url] = b"different current comparison"
        produced = world.analysis.review_context(
            world.bill("3039"),
            "commission_review",
            (
                *(
                    ReviewSource(
                        identity=document.identity,
                        document=TextDocument(url=document.url, kind="print"),
                        role=document.role,
                        snapshot=document,
                    )
                    for document in documents
                ),
                ReviewSource(
                    identity="missing-optional-comparison",
                    document=missing,
                    role="comparison_text",
                    expected_digest="f" * 64,
                ),
            ),
        )
        assert produced is not None, "the real producer retains complete commission sources"
        source = produced
        assert isinstance(
            world.analysis.request_document_review(world.bill("3039"), source), Waiting
        )
        (intent,) = world.repo.list_queued_batch_intents()
    else:
        world, source, intent = queued_review(purpose)
    submit_review(world, intent)
    world.batch.resolve()
    world.analysis.collect_batches()
    world.analysis.acknowledge_batch_costs()
    first = world.analysis.request_document_review(world.bill("3039"), source)
    assert isinstance(first, DocumentReviewRecord), "the fake provider completed the first review"
    world.repo.save_document_review(10, "3039", first)
    original = next(row for row in world.repo.list_computation_results(10, "3039"))
    document = source.documents[0]
    text = document.text + " Nowy urzędowy akapit."
    changed = source.model_copy(
        update={
            "documents": (
                document.model_copy(
                    update={
                        "text": text,
                        "sha256": hashlib.sha256(text.encode()).hexdigest(),
                        "normalized_sha256": text_digest(text),
                    }
                ),
                *source.documents[1:],
            )
        }
    )
    assert isinstance(world.analysis.request_document_review(world.bill("3039"), changed), Waiting)
    (second,) = world.repo.list_queued_batch_intents()
    submit_review(world, second)
    world.batch.resolve()
    world.analysis.collect_batches()
    world.analysis.acknowledge_batch_costs()
    latest = world.analysis.request_document_review(world.bill("3039"), changed)
    assert isinstance(latest, DocumentReviewRecord), "the second paid review replaces the first"
    world.repo.save_document_review(10, "3039", latest)
    world.run(track=False)
    world.clock.advance(days=200)
    world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
    world.repo.restore(world.repo.dump())
    assert not world.repo.load_analysis_memo()
    world.analysis.start_run()
    return world, source, original, latest


def test_source_produced_missing_optional_input_keeps_its_unknown_sentinel_on_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, source, original, latest = replaced_review("commission_review", missing_optional=True)
    missing = source.documents[-1]
    assert missing.identity == "missing-optional-comparison"
    assert (missing.text, missing.sha256, missing.normalized_sha256) == ("", "", "")
    assert missing.complete is False and missing.file_sha256 is None

    def forbid_preparation(request: BatchRequest) -> BatchRequest:
        raise AssertionError("the recorded missing-input question already has a paid answer")

    monkeypatch.setattr(world.batch, "prepare_request", forbid_preparation)
    reused = world.analysis.request_document_review(world.bill("3039"), source)
    assert isinstance(reused, DocumentReviewRecord)
    assert reused.context == source and reused.context.documents[-1] == missing
    assert reused.computation_id == original.result_id != latest.computation_id
    assert reused.request_sha256 == original.request_sha256
    assert reused.input_tokens == reused.output_tokens == 0
    assert world.repo.get_computation_result(original.result_id) == original
    assert not world.repo.list_queued_batch_intents()


@pytest.mark.parametrize("purpose", PURPOSES)
def test_replaced_review_reuses_exact_paid_history_after_memo_cleanup(
    purpose: ReviewPurpose, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, source, original, latest = replaced_review(purpose)
    before = world.bill("3039")
    world.repo.upsert_summary(
        before.summary.model_copy(update={"title": "A different current Bill title"}),
        now=world.clock.now(),
    )
    requests, messages = list(world.batch.submitted), list(world.publisher.sent)
    applications = world.repo.list_result_applications(10, "3039")

    def forbid_preparation(request: BatchRequest) -> BatchRequest:
        raise AssertionError("exact durable review reuse must not prepare another provider request")

    monkeypatch.setattr(world.batch, "prepare_request", forbid_preparation)

    def exhausted(key: str) -> BatchJob:
        return BatchJob(state="failed", since=world.clock.now(), submissions=2)

    monkeypatch.setattr(world.repo, "batch_job", exhausted)
    assert not world.analysis.review_exhausted(10, source)

    for _ in range(2):
        reused = world.analysis.request_document_review(world.bill("3039"), source)
        assert isinstance(reused, DocumentReviewRecord)
        assert reused.context == source and reused.context.title != world.bill("3039").summary.title
        assert reused.computation_id == original.result_id != latest.computation_id
        assert reused.request_sha256 == original.request_sha256
        assert reused.input_tokens == reused.output_tokens == 0
    assert world.repo.get_computation_result(original.result_id) == original
    assert len(world.repo.list_computation_results(10, "3039")) == 2
    assert world.repo.list_result_applications(10, "3039") == applications
    assert not world.repo.list_queued_batch_intents()
    assert world.batch.submitted == requests and world.publisher.sent == messages
    after = world.bill("3039")
    assert (after.status, after.analysis_generation) == (before.status, before.analysis_generation)
    assert {
        "consultation_review": after.consultation_review,
        "commission_review": after.commission_review,
        "act_application": after.act_application_review,
    }[purpose] == latest


def test_changed_body_cannot_reuse_a_review_by_keeping_its_old_declared_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, source, original, _ = replaced_review("consultation_review")
    document = source.documents[0]
    falsified = source.model_copy(
        update={"documents": (document.model_copy(update={"text": document.text + " ZMIANA"}),)}
    )
    assert falsified.memo_key(10) == source.memo_key(10)

    def forbid_preparation(request: BatchRequest) -> BatchRequest:
        raise AssertionError("a changed body requires an ordinary new request")

    monkeypatch.setattr(world.batch, "prepare_request", forbid_preparation)

    with pytest.raises(AssertionError, match="changed body"):
        world.analysis.request_document_review(world.bill("3039"), falsified)
    assert world.repo.get_computation_result(original.result_id) == original


@pytest.mark.parametrize("change", ["text", "comparison"])
def test_mismatched_context_does_not_bypass_the_existing_submission_limit(
    change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, source, original, _ = replaced_review("consultation_review")
    current = source.model_copy(
        update={
            "documents": (
                source.documents[0].model_copy(
                    update={"text": source.documents[0].text + " ZMIANA"}
                ),
            )
        }
        if change == "text"
        else {"comparison_sha256": "f" * 64}
    )

    def exhausted(key: str) -> BatchJob:
        return BatchJob(state="failed", since=world.clock.now(), submissions=2)

    monkeypatch.setattr(world.repo, "batch_job", exhausted)
    assert world.analysis.review_exhausted(10, current)
    with pytest.raises(RuntimeError, match="submission limit"):
        world.analysis.request_document_review(world.bill("3039"), current)
    assert world.repo.get_computation_result(original.result_id) == original
    assert not world.repo.list_queued_batch_intents()


@pytest.mark.parametrize("purpose", ["consultation_review", "commission_review"])
def test_durable_history_does_not_bypass_disabled_review_purposes(purpose: ReviewPurpose) -> None:
    world, source, original, _ = replaced_review(purpose)
    disabled = World(
        batch=True,
        batch_kinds=frozenset({purpose}),
        consultation_reviews_enabled=False,
        commission_reviews_enabled=False,
    )
    disabled.repo.restore(world.repo.dump())
    disabled.analysis.start_run()
    answer = disabled.analysis.request_document_review(disabled.bill("3039"), source)
    assert isinstance(answer, Waiting)
    assert not disabled.repo.list_queued_batch_intents() and not disabled.batch.submitted
    assert disabled.repo.get_computation_result(original.result_id) == original

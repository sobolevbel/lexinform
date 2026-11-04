from dataclasses import replace

import pytest

from lexinform.commission_reviews import commission_sources
from lexinform.models import (
    DeliveryPlan,
    DocumentReviewRecord,
    Publication,
    PublicationKind,
    PublicationStatus,
    iso_week,
    validate_review,
)
from lexinform.services.analysis import Waiting
from lexinform.services.tracking_result import TrackingResult
from tests.commission_cases import PROPOSAL, RESPONSE, citation, context, finding, review
from tests.fakes import FakeTextExtractor
from tests.harness import RCL, World, rcl_document
from tests.scenario.test_rcl_legal_commission import start_legal_review


def prepared_world(*, workers: int = 1, enabled: bool = True) -> World:
    w = World(
        batch=True,
        batch_kinds=frozenset({"commission_review"}),
        commission_reviews_enabled=enabled,
        workers=workers,
    )
    project = start_legal_review(w)
    source = rcl_document(42, "Uwagi RCL.docx", created=w.clock.now().date())
    w.rcl.put(
        project.model_copy(
            update={
                "stages": tuple(
                    stage.model_copy(update={"loose_documents": (source,)})
                    if stage.is_legal_commission
                    else stage
                    for stage in project.stages
                )
            }
        )
    )
    w.rcl.files[source.url] = b"remarks"
    assert isinstance(w.extractor, FakeTextExtractor)
    w.extractor.by_content[b"remarks"] = PROPOSAL + "\n" + ("Uzasadnienie uwagi. " * 40)
    proposal = citation("rcl/42", PROPOSAL)
    item = finding().model_copy(
        update={
            "proposal_evidence": proposal,
            "outcome": "unknown",
            "evidence": None,
            "text_evidence": None,
            "comparison_evidence": None,
            "text_change": None,
        }
    )
    w.batch.secondary_script[(RCL, "commission_review")] = review(item)
    return w


@pytest.mark.parametrize("workers", [1, 4])
def test_automatic_review_batch_survives_restore_and_publishes_once(workers: int) -> None:
    w = prepared_world(workers=workers)
    report = w.run()
    assert report.ok
    before = w.bill(RCL)
    assert before.review_work and before.review_work[0].context is not None
    assert len(w.batch.submitted) == 1
    assert w.batch.submitted[0].question.kind == "commission_review"
    assert not before.consultation_review and not before.commission_review
    w.repo.restore(w.repo.dump())
    w.batch.resolve()

    report = w.run()

    bill = w.bill(RCL)
    assert report.ok and report.commission_reviews_posted == 1
    assert bill.status == before.status and bill.analysis == before.analysis
    assert bill.commission_review is not None and not bill.review_work
    assert bill.commission_review.context.documents == before.review_work[0].context.documents
    assert not bill.consultation_review
    text = w.publisher.texts(PublicationKind.COMMISSION_REVIEW)[0]
    assert "Снизить сбор" in text and "Если поправку примут" in text
    assert "НЕПОДТВЕРЖДЁННОЕ РЕЗЮМЕ" not in text
    assert "Minister Cyfryzacji" not in text
    pub = w.publication(RCL, PublicationKind.COMMISSION_REVIEW)
    assert pub is not None and pub.status is PublicationStatus.SENT
    w.repo.restore(w.repo.dump())
    w.clock.advance(days=4)
    assert w.run().ok
    assert len(w.batch.submitted) == 1
    assert len(w.publisher.texts(PublicationKind.COMMISSION_REVIEW)) == 1


def test_commission_flag_is_independent_from_paused_consultation_reviews() -> None:
    w = prepared_world(enabled=False)
    assert w.run().ok
    assert not w.bill(RCL).review_work and not w.batch.submitted
    assert isinstance(w.analysis.request_document_review(w.bill(RCL), context()), Waiting)
    assert not w.repo.list_queued_batch_intents()


def test_pausing_preserves_queued_work_and_prevents_new_submissions() -> None:
    w = prepared_world()
    w.run()
    work = w.bill(RCL).review_work
    assert work and work[0].context is not None
    paused = replace(
        w.container,
        settings=w.container.settings.model_copy(update={"commission_reviews_enabled": False}),
        analysis_options_override=None,
    )
    w.batch.resolve()
    paused.analysis_service().collect_batches()
    result = TrackingResult()
    paused.document_reviews(dry_run=False).advance(result, publish=True)
    assert w.bill(RCL).review_work == work
    assert not w.publisher.texts(PublicationKind.COMMISSION_REVIEW)


@pytest.mark.parametrize(
    "name",
    ["Zaproszenie KP.pdf", "Klauzula.pdf", "Projekt ustawy.docx", "Raport z konsultacji.docx"],
)
def test_uploads_without_remarks_do_not_request_a_review(name: str) -> None:
    w = prepared_world()
    project = w.rcl.get_project(12414100)
    source = rcl_document(43, name, created=w.clock.now().date())
    w.rcl.put(
        project.model_copy(
            update={
                "stages": tuple(
                    stage.model_copy(update={"loose_documents": (source,)})
                    if stage.is_legal_commission
                    else stage
                    for stage in project.stages
                )
            }
        )
    )
    w.rcl.files[source.url] = b"unrelated"
    assert w.run().ok
    assert not w.batch.submitted and not w.bill(RCL).review_work
    assert not commission_sources(w.bill(RCL))


def test_saved_review_keeps_comparison_snapshot_after_restore() -> None:
    w = prepared_world()
    w.run()
    ctx = context()
    record = DocumentReviewRecord(
        review=validate_review(review(), ctx), context=ctx, created_at=w.clock.now()
    )
    w.repo.save_document_review(w.bill(RCL).term, RCL, record)
    w.repo.restore(w.repo.dump())
    assert w.bill(RCL).commission_review == record
    assert w.bill(RCL).consultation_review is None


def test_pending_context_is_frozen_and_later_response_gets_its_own_review() -> None:
    w = prepared_world()
    w.run()
    original = w.bill(RCL).review_work
    w.clock.advance(days=4)
    project = w.rcl.projects[12414100]
    response = rcl_document(44, "Odpowiedź na uwagi RCL.docx", created=w.clock.now().date())
    w.rcl.put(
        project.model_copy(
            update={
                "modified": w.clock.now().date(),
                "stages": tuple(
                    stage.model_copy(
                        update={
                            "modified": w.clock.now().date(),
                            "loose_documents": (*stage.loose_documents, response),
                        }
                    )
                    if stage.is_legal_commission and stage.loose_documents is not None
                    else stage
                    for stage in project.stages
                ),
            }
        )
    )
    w.rcl.files[response.url] = b"response"
    assert isinstance(w.extractor, FakeTextExtractor)
    w.extractor.by_content[b"response"] = RESPONSE + "\n\n" + ("Uzasadnienie stanowiska. " * 40)
    assert w.run().ok
    assert w.bill(RCL).review_work == original
    assert len(w.batch.submitted) == 1
    w.batch.resolve()
    assert w.run().ok
    first = w.bill(RCL).commission_review
    assert first is not None
    assert first.review.commission_findings[0].outcome == "unknown"
    assert all(doc.url != response.url for doc in first.context.documents)
    item = finding().model_copy(
        update={
            "proposal_evidence": citation("rcl/42", PROPOSAL),
            "evidence": citation("rcl/44", RESPONSE),
            "text_change": None,
        }
    )
    w.batch.secondary_script[(RCL, "commission_review")] = review(item)
    assert w.run(full_track=True).ok
    assert len(w.batch.submitted) == 2
    w.batch.resolve()
    assert w.run().ok
    second = w.bill(RCL).commission_review
    assert second is not None and second != first
    assert second.review.commission_findings[0].outcome == "accepted"
    assert len(w.publisher.texts(PublicationKind.COMMISSION_REVIEW)) == 2


@pytest.mark.parametrize(
    "status", [PublicationStatus.QUEUED, PublicationStatus.FAILED, PublicationStatus.UNKNOWN]
)
@pytest.mark.parametrize("continuation", [False, True])
def test_paused_delivery_keeps_payload_and_attempts(
    status: PublicationStatus, continuation: bool
) -> None:
    w = prepared_world()
    ctx = context()
    record = DocumentReviewRecord(
        review=validate_review(review(), ctx), context=ctx, created_at=w.clock.now()
    )
    w.repo.save_document_review(w.bill(RCL).term, RCL, record)
    root = w.container.poster(dry_run=False).prepare_message(
        w.bill(RCL), PublicationKind.COMMISSION_REVIEW
    )
    assert root.id is not None
    pub_id = root.id
    if continuation:
        w.repo.mark_publication(root.id, PublicationStatus.SENT, count_attempt=False)
        pub_id = w.repo.create_publication(
            Publication(
                term=w.bill(RCL).term,
                number=RCL,
                channel_id="@test",
                kind=PublicationKind.MESSAGE_PART,
                status=status,
                created_at=w.clock.now(),
                ref="commission/2",
                delivery=DeliveryPlan(
                    bill_json=w.bill(RCL).model_dump_json(),
                    rendered_text="Продолжение",
                    continuation_root=root.id,
                    continuation_parent=root.id,
                ),
            )
        )
    w.repo.mark_publication(pub_id, status, count_attempt=True)
    saved = w.repo.publication_by_id(pub_id)
    assert saved is not None
    paused = replace(
        w.container,
        settings=w.container.settings.model_copy(update={"commission_reviews_enabled": False}),
        analysis_options_override=None,
    )
    w.repo.restore(w.repo.dump())
    assert not paused.delivery_service(dry_run=False).run().errors
    assert not paused.poster(dry_run=False).deliver(saved)
    assert w.repo.publication_by_id(pub_id) == saved
    assert not w.publisher.texts(PublicationKind.COMMISSION_REVIEW)
    assert not w.publisher.texts(PublicationKind.MESSAGE_PART)


@pytest.mark.parametrize("substantive", [False, True])
def test_only_remarks_without_reader_consequences_go_to_digest(substantive: bool) -> None:
    w = World(reader_attention="on", commission_reviews_enabled=True)
    start_legal_review(w)
    item = finding()
    if not substantive:
        item = item.model_copy(
            update={"text_change": None, "practical_impact": "", "affected_groups": ()}
        )
    ctx = context()
    record = DocumentReviewRecord(
        review=validate_review(review(item), ctx), context=ctx, created_at=w.clock.now()
    )
    w.repo.save_document_review(w.bill(RCL).term, RCL, record)
    pub = w.container.poster(dry_run=False).prepare_message(
        w.bill(RCL), PublicationKind.COMMISSION_REVIEW, today=w.clock.now().date()
    )
    assert pub.delivery is not None and pub.delivery.attention is not None
    assert pub.delivery.destination == ("direct" if substantive else "digest_only")
    assert pub.delivery.attention.importance == ("important" if substantive else "routine")


def test_missing_batch_kind_prevents_even_a_prepared_commission_intent_from_submitting() -> None:
    w = prepared_world()
    assert isinstance(w.analysis.request_document_review(w.bill(RCL), context()), Waiting)
    intents = w.repo.list_queued_batch_intents()
    assert len(intents) == 1
    disabled = replace(
        w.container,
        settings=w.container.settings.model_copy(
            update={"llm_batch_kinds": frozenset({"analysis"})}
        ),
        analysis_options_override=None,
    )
    disabled.analysis_service().start_run()
    disabled.analysis_service().submit_queued_batches()
    assert w.repo.list_queued_batch_intents() == intents
    assert not w.batch.submitted


@pytest.mark.parametrize("frozen", [False, True])
def test_pause_cannot_be_bypassed_by_digest_or_its_frozen_retry(frozen: bool) -> None:
    w = World(reader_attention="on", commission_reviews_enabled=True)
    start_legal_review(w)
    item = finding().model_copy(
        update={"text_change": None, "practical_impact": "", "affected_groups": ()}
    )
    ctx = context()
    record = DocumentReviewRecord(
        review=validate_review(review(item), ctx), context=ctx, created_at=w.clock.now()
    )
    w.repo.save_document_review(w.bill(RCL).term, RCL, record)
    source = w.container.poster(dry_run=False).prepare_message(
        w.bill(RCL), PublicationKind.COMMISSION_REVIEW, today=w.clock.now().date()
    )
    assert source.id is not None and source.delivery is not None
    assert source.delivery.destination == "digest_only"
    service = w.container.digest_service(dry_run=False)
    assert service is not None
    ref = iso_week(w.clock.now().date())
    saved_owner = None
    if frozen:
        w.publisher.fail_digest_attempt = 1
        result = service.publish(ref)
        assert result.failed
        owner_id = w.repo.digest_owner(source.id)
        assert owner_id is not None
        saved_owner = w.repo.publication_by_id(owner_id)
        assert saved_owner is not None and saved_owner.status is PublicationStatus.FAILED
    attempts = w.publisher.digest_send_attempts
    saved_source = w.repo.publication_by_id(source.id)
    paused = replace(
        w.container,
        settings=w.container.settings.model_copy(update={"commission_reviews_enabled": False}),
        analysis_options_override=None,
    )
    w.repo.restore(w.repo.dump())
    paused_service = paused.digest_service(dry_run=False)
    assert paused_service is not None
    week = paused_service.build(ref)
    assert not any(source.id in entry.source_publication_ids for entry in week.updates)
    if frozen:
        result = paused_service.publish(ref)
        assert not result.published and not result.failed
        assert saved_owner is not None and saved_owner.id is not None
        assert not paused.poster(dry_run=False).deliver(saved_owner)
        assert w.repo.publication_by_id(saved_owner.id) == saved_owner
        assert w.publisher.digest_send_attempts == attempts
    assert w.repo.publication_by_id(source.id) == saved_source
    assert saved_source is not None and saved_source.status is PublicationStatus.QUEUED

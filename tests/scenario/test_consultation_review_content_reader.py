import sqlite3
from contextlib import closing
from datetime import timedelta
from unittest.mock import patch

import pytest

from lexinform.adapters import review_content_evidence
from lexinform.models import (
    Bill,
    BillStatus,
    DocumentReviewRecord,
    Publication,
    PublicationKind,
    PublicationStatus,
    ReviewSource,
    ReviewWork,
    TextDocument,
)
from lexinform.models.original_delivery_content import OriginalDeliveryEvidence
from lexinform.models.reviews import ConsultationRow, EvidenceCitation, validate_review
from lexinform.services.tracking_result import TrackingResult
from tests.checkpoint_state import saved_state
from tests.harness import CHANNEL, TERM, World, rcl_document, summary
from tests.scenario.test_commission_review_content_reader import with_content
from tests.scenario.test_document_reviews import context, finding, review

NUMBER = "RCL/12282650"


def queue_notice(world: World, sources: tuple[ReviewSource, ...]) -> Publication:
    now = world.clock.now()
    world.repo.upsert_summary(summary(NUMBER, "Controlled consultation review"), now=now)
    world.repo.set_status(TERM, NUMBER, BillStatus.ANALYZED)
    world.repo.create_publication(
        Publication(
            term=TERM,
            number=NUMBER,
            kind=PublicationKind.NEW_BILL,
            channel_id=CHANNEL,
            created_at=now,
            sent_at=now,
            status=PublicationStatus.SENT,
            message_id=100,
        )
    )
    world.analysis.start_run()
    context = world.analysis.review_context(world.bill(NUMBER), "consultation_review", sources)
    assert context is not None, "the fixture retains actual review inputs"
    assert len(context.source_observation_ids) == len(sources)
    work = ReviewWork(
        memo_key=context.memo_key(TERM),
        purpose="consultation_review",
        context=context,
        since=now,
        baseline_day=now.date(),
        source_key="review-input:" + "a" * 64,
    )
    world.repo.save_review_work(TERM, NUMBER, (work,))
    answer = finding(outcome="unknown").model_copy(
        update={"proposal": "Synthetic fixture finding", "evidence": None}
    )
    world.batch.secondary_script[(NUMBER, "consultation_review")] = review(answer)
    reviews = world.container.document_reviews(dry_run=False)
    reviews.advance(TrackingResult(), publish=False)
    assert len(world.repo.list_queued_batch_intents()) == 1
    world.analysis.submit_queued_batches()
    world.batch.resolve()
    world.analysis.collect_batches()
    world.analysis.acknowledge_batch_costs()
    reviews.advance(TrackingResult(), publish=False)
    result = world.publication(NUMBER, PublicationKind.CONSULTATION_REVIEW)
    assert result is not None and result.id is not None
    return result


def prepared() -> tuple[World, Publication]:
    world = World(
        batch=True,
        consultation_reviews_enabled=True,
        reader_attention="off",
        batch_kinds=frozenset({"consultation_review"}),
    )
    fixture = rcl_document(1, "Odpowiedź.pdf")
    world.rcl.files[fixture.url] = b"%PDF"
    queued = queue_notice(
        world,
        (
            ReviewSource(
                identity="response/1",
                role="response",
                document=TextDocument(url=fixture.url, kind="rcl"),
            ),
        ),
    )
    return world, queued


def test_consultation_content_reader_preserves_legacy_and_verifies_new_link() -> None:
    world, queued = prepared()
    assert queued.id is not None
    with patch.object(world.repo, "find_review_application_id", return_value=None):
        assert world.container.poster(dry_run=False).deliver(queued)
    legacy = world.repo.get_publication_content(queued.id)
    assert legacy is not None and legacy.original_delivery_evidence is not None
    assert legacy.original_delivery_evidence.review_application_id is None
    cutoff = world.clock.now() + timedelta(days=200)
    world.repo.prune_history(before=cutoff, memo_before=cutoff)
    old = world.repo.dump()
    with closing(sqlite3.connect(":memory:")) as raw:
        raw.executescript(old)
        ((application_id,),) = raw.execute(
            "SELECT id FROM result_applications"
            " WHERE json_extract(application_json, '$.target.kind')='document_review'"
        ).fetchall()
    evidence = legacy.original_delivery_evidence.model_copy(
        update={"review_application_id": application_id}
    )
    with pytest.raises(ValueError, match="commission review application"):
        OriginalDeliveryEvidence.model_validate(
            evidence.model_copy(update={"commission_review_application_id": application_id})
        )
    with pytest.raises(ValueError, match="consultation notice"):
        OriginalDeliveryEvidence.model_validate(
            evidence.model_copy(update={"kind": PublicationKind.COMMISSION_REVIEW})
        )
    future = legacy.model_copy(update={"original_delivery_evidence": evidence})
    future_dump = with_content(old, legacy, future)
    with saved_state(old) as durable:
        assert durable.repo.dump() == old
        durable.repo.restore(future_dump)
        assert durable.repo.get_publication_content(queued.id) == future
        assert durable.repo.dump() == future_dump
        wrong = evidence.model_copy(update={"review_application_id": application_id + 500})
        invalid = future.model_copy(update={"original_delivery_evidence": wrong})
        with pytest.raises(ValueError, match="invalid_content_history"):
            durable.repo.restore(with_content(old, legacy, invalid))
        assert durable.repo.dump() == future_dump


def test_consultation_writer_preserves_html_with_missing_and_invalid_application() -> None:

    normal, queued = prepared()
    assert queued.id is not None
    assert normal.container.poster(dry_run=False).deliver(queued)
    linked = normal.repo.get_publication_content(queued.id)
    assert linked is not None and linked.original_delivery_evidence is not None
    assert linked.original_delivery_evidence.review_application_id is not None
    assert linked.original_delivery_evidence.commission_review_application_id is None

    for absent in (False, True):
        fallback, old_queued = prepared()
        if absent:
            missing = patch.object(fallback.repo, "find_review_application_id", return_value=None)
        else:
            missing = patch.object(
                review_content_evidence,
                "read_result_application",
                side_effect=ValueError("invalid"),
            )
        with missing:
            assert fallback.container.poster(dry_run=False).deliver(old_queued)
        assert old_queued.id is not None
        old = fallback.repo.get_publication_content(old_queued.id)
        assert old is not None and old.original_delivery_evidence is not None
        assert old.original_delivery_evidence.review_application_id is None
        assert fallback.publisher.sent == normal.publisher.sent
        assert fallback.batch.submitted == normal.batch.submitted
    cutoff = normal.clock.now() + timedelta(days=200)
    normal.repo.prune_history(before=cutoff, memo_before=cutoff)
    dump = normal.repo.dump()
    normal.repo.restore(dump)
    assert normal.repo.dump() == dump
    assert normal.repo.get_publication_content(queued.id) == linked


def test_word_row_remark_survives_compact_delivery_snapshot() -> None:
    world = World(consultation_reviews_enabled=True, reader_attention="off")
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    locator = "word-xml:word/document.xml:0/1/2"
    source_context = context("Stowarzyszenie\tDokumenty\tUwaga uwzględniona.")
    source = source_context.documents[0]
    source = source.model_copy(
        update={
            "consultation_rows": (
                ConsultationRow(locator=locator, label="93", start=0, end=len(source.text)),
            )
        }
    )
    source_context = source_context.model_copy(update={"documents": (source,)})
    original = finding().model_copy(
        update={
            "evidence": EvidenceCitation(document_id=source.identity, locator=locator, quote="")
        }
    )
    record = DocumentReviewRecord(
        review=validate_review(review(original), source_context),
        context=source_context,
        created_at=world.clock.now(),
    )
    bill = world.bill("3039").model_copy(update={"consultation_review": record})
    poster = world.container.poster(dry_run=False)

    publication = poster.prepare_message(bill, PublicationKind.CONSULTATION_REVIEW, ref="test")

    assert publication.delivery is not None, "consultation review freezes a delivery plan"
    frozen = Bill.model_validate_json(publication.delivery.bill_json)
    assert frozen.consultation_review is not None, "the snapshot retains review evidence"
    assert not frozen.consultation_review.context.documents[0].text
    assert not frozen.consultation_review.context.documents[0].consultation_rows
    assert poster.deliver(publication)
    assert "Замечание № 93" in world.publisher.texts(PublicationKind.CONSULTATION_REVIEW)[0]

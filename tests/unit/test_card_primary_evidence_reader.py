import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from tools.content_history_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.input_receipts import primary_input_receipt
from lexinform.models import (
    AnalysisRecord,
    ArtifactMetadata,
    BatchIntent,
    Bill,
    BillStatus,
    ComputationInputBinding,
    ComputationResult,
    ContentVersion,
    ContentVersionSummary,
    DeliveryPlan,
    DocumentSelectionRecord,
    LlmBatch,
    LlmBatchItem,
    Publication,
    PublicationKind,
    PublicationStatus,
    ResultApplication,
    SelectionArtifactReference,
    SelectionExtractionReference,
    computation_id,
)
from lexinform.models.content import CardPrimaryEvidence
from lexinform.primary_projections import primary_record_sha256
from lexinform.services.content_history import prepare_publication_content
from lexinform.services.content_inspection import inspect_content
from lexinform.services.message_parts import continuation_snapshot
from tests.fakes import FixedClock
from tests.unit.test_content_sources import Case
from tests.unit.test_content_sources import case as case
from tests.unit.test_input_receipts import NOW, SOURCE, meta, request, selection
from tests.unit.test_physical_extraction import docx, extractor


def card_parents(case: Case) -> tuple[Publication, CardPrimaryEvidence]:
    repo = case.repo
    repo.upsert_summary(case.bill.summary, now=NOW)
    repo.set_status(10, "3039", BillStatus.ANALYZED)
    repo.mark_batch_item_accounted(case.item.batch_id, case.item.custom_id, at=NOW)
    raw = docx(SOURCE)
    extracted = extractor().extract_document_with_provenance(raw)
    result = extracted.result
    assert extracted.document.text == SOURCE, "the prepared question uses the entire test leaf"
    artifact = ArtifactMetadata(sha256=result.artifact_sha256, byte_size=len(raw), recorded_at=NOW)
    original = selection()
    part = original.parts[0].model_copy(update={"sha256": artifact.sha256, "text_end": len(SOURCE)})
    selected = original.model_copy(update={"parts": (part,)})
    retained = DocumentSelectionRecord.from_selection(selected)
    observation = case.observation.model_copy(update={"selection_id": retained.selection_id})
    repo.save_artifact_metadata(artifact)
    repo.save_extraction_result(result, recorded_at=NOW)
    repo.save_source_observation(retained, observation)
    repo.save_selection_artifact(
        SelectionArtifactReference(
            selection_id=retained.selection_id,
            part_scope="parts",
            part_index=0,
            artifact_sha256=artifact.sha256,
        )
    )
    repo.save_selection_extraction(
        SelectionExtractionReference(
            selection_id=retained.selection_id,
            extraction_id=result.result_id,
            artifact_sha256=artifact.sha256,
        )
    )
    frozen = request().model_copy(update={"custom_id": "card-input"})
    receipt = primary_input_receipt(
        frozen,
        selected,
        projection=None,
        known_selection=retained,
        body=artifact,
        extraction=result,
    )
    binding = ComputationInputBinding(term=10, number="3039", reuse_key="frozen-key")
    intent = BatchIntent.from_request(
        request=frozen,
        meta=meta(receipt).model_copy(
            update={
                "source_url": part.url,
                "selection": retained.selection,
                "generation": 2,
                "text": SOURCE,
            }
        ),
        provider="openai",
        created_at=NOW,
    )
    batch_id = "card-evidence"
    item = LlmBatchItem.from_intent(batch_id, intent)
    repo.save_batch_intent(intent)
    repo.save_llm_batch(
        LlmBatch(
            batch_id=batch_id,
            provider="openai",
            call_kind="analysis",
            submitted_at=NOW,
            status="ended",
            request_count=1,
            estimated_cost_usd=0.01,
        ),
        [item],
    )
    repo.delete_batch_intents([item.custom_id])
    repo.mark_llm_batch_item_consumed(batch_id, item.custom_id, consumed_at=NOW)
    repo.mark_batch_item_accounted(batch_id, item.custom_id, at=NOW)
    repo.mark_llm_batch_forgotten(batch_id, forgotten_at=NOW)
    base = case.paid.record
    assert isinstance(base, AnalysisRecord), "the paid fixture is a primary analysis"
    answer = base.model_copy(
        update={"computation_id": computation_id("openai", batch_id, item.custom_id)}
    )
    paid = ComputationResult.model_validate(
        {
            **case.paid.model_dump(),
            "batch_id": batch_id,
            "custom_id": item.custom_id,
            "input_binding": binding,
            "record": answer,
        }
    )
    repo.save_computation_result(paid)
    repo.save_analysis_memo(
        "expired-card-memo", answer.model_dump_json(), term=10, number="3039", used_at=NOW
    )
    collection = ResultApplication(
        result_id=paid.result_id,
        term=10,
        number="3039",
        generation=2,
        outcome="ready",
        reason="current_generation",
        observed_generation=2,
        observed_status=None,
        created_at=NOW,
    )
    repo.save_result_application(collection)
    repo.save_result_application(
        collection.model_copy(
            update={
                "phase": "projection",
                "reason": "batch_result",
                "outcome": "applied",
                "projected_generation": 3,
            }
        )
    )
    with closing(sqlite3.connect(case.path)) as connection:
        projection = connection.execute(
            "SELECT id FROM result_applications WHERE result_id=? AND phase='projection'",
            (paid.result_id,),
        ).fetchone()
    assert projection is not None, "the fixture captured the actual SQL application identity"
    bill = case.bill.model_copy(update={"analysis": answer, "analysis_generation": 3})
    pub_id = repo.create_publication(
        Publication(
            term=bill.term,
            number=bill.number,
            kind=PublicationKind.NEW_BILL,
            status=PublicationStatus.QUEUED,
            channel_id="technical",
            created_at=NOW,
            delivery=DeliveryPlan(bill_json=bill.model_dump_json(), rendered_text="Frozen card"),
        )
    )
    pub = repo.publication_by_id(pub_id)
    assert pub is not None, "the fixture persists a fresh direct operation"
    return pub, CardPrimaryEvidence(
        projection_application_id=projection[0],
        source_observation_id=observation.observation_id,
    )


def store_marked_card(case: Case) -> tuple[Publication, ContentVersion]:
    pub, reference = card_parents(case)
    prepared = prepare_publication_content(
        case.repo,
        FixedClock(NOW),
        pub,
        formatter_version="telegram-format-v2",
        language="ru",
        card_history_parent="original",
    )
    assert prepared is not None, "the fixture prepares the current compact original"
    plain, snapshots = prepared
    assert "card_primary_evidence" not in plain.model_dump_json()
    value = ContentVersion.model_validate(
        {**plain.model_dump(), "card_primary_evidence": reference}
    )
    assert value.content_id != plain.content_id
    for snapshot in snapshots:
        case.repo.save_content_snapshot(snapshot)
    case.repo.save_content_version(value)
    assert pub.id is not None, "the fixture has an original publication identity"
    case.repo.bind_publication_content(pub.id, value.content_id)
    return pub, value


def test_card_primary_evidence_survives_retention_but_never_confirms_an_unsent_card(
    case: Case,
    tmp_path: Path,
) -> None:
    pub, value = store_marked_card(case)
    assert pub.id is not None, "the original operation has a physical identity"
    unconfirmed = inspect_content(case.repo, pub)
    assert unconfirmed is not None and unconfirmed.current_id is None
    case.repo.mark_publication(pub.id, PublicationStatus.SENT, message_id=10, sent_at=NOW)
    first = case.repo.prune_history(
        before=NOW + timedelta(days=1), memo_before=NOW - timedelta(days=89)
    )
    assert first == {"delivery_payloads": 1, "analysis_memos": 0, "batch_payloads": 1}
    assert case.repo.get_publication_content(pub.id) == value
    second = case.repo.prune_history(
        before=NOW + timedelta(days=91), memo_before=NOW + timedelta(days=1)
    )
    assert second == {"delivery_payloads": 0, "analysis_memos": 1, "batch_payloads": 0}
    sql = case.repo.dump()
    case.repo.restore(sql)

    assert case.repo.dump() == sql
    sent = case.repo.publication_by_id(pub.id)
    assert sent is not None and sent.delivery is None
    assert case.repo.get_publication_content(pub.id) == value
    confirmed = inspect_content(case.repo, sent)
    assert confirmed is not None and confirmed.current_id == value.content_id
    target = tmp_path / "checkpoint.db"
    with closing(SqliteBillRepository(target)) as copy:
        copy.restore(sql)
    report = audit(target, publication_id=pub.id)
    assert report["status"] == "recorded" and not report["findings"]
    assert report["histories"][0]["current_content_id"] == value.content_id
    reference = value.card_primary_evidence
    assert reference is not None, "the fixture stores the exact validated references"
    assert report["histories"][0]["versions"][0]["card_primary_evidence"] == {
        "profile": "card-primary-evidence-v1",
        "source_observation_id": reference.source_observation_id,
        "projection_application_id": reference.projection_application_id,
    }
    assert "card_primary_evidence" not in ContentVersionSummary.from_version(value).model_dump()


@pytest.mark.parametrize(
    "mutation",
    [
        "DELETE FROM source_observations WHERE observation_id='{source_observation_id}'",
        "DELETE FROM result_applications WHERE id={projection_application_id}",
        "UPDATE result_applications SET application_json=json_set(application_json,"
        "'$.reason','retained_result') WHERE id={projection_application_id}",
    ],
)
def test_restore_rejects_a_changed_or_missing_card_primary_parent(
    case: Case, mutation: str
) -> None:
    _, value = store_marked_card(case)
    before = case.repo.dump()
    reference = value.card_primary_evidence
    assert reference is not None, "the synthetic card records exact parents"

    with pytest.raises(ValueError, match="invalid_content_history"):
        case.repo.restore(before + "\n" + mutation.format(**reference.model_dump()) + ";")

    assert case.repo.dump() == before


def test_card_evidence_rejects_the_same_source_url_from_another_selection(case: Case) -> None:
    _, value = store_marked_card(case)
    reference = value.card_primary_evidence
    assert reference is not None, "the synthetic card records exact parents"
    replacement = reference.model_copy(
        update={"source_observation_id": case.observation.observation_id}
    )
    substituted = value.model_copy(update={"card_primary_evidence": replacement})

    with pytest.raises(ValueError, match="card_primary_source_scope"):
        case.repo.save_content_version(substituted)


def test_frozen_card_requires_exact_paid_record_before_payload_expires(case: Case) -> None:
    pub, _ = store_marked_card(case)
    assert pub.id is not None, "the test has a saved physical card"
    before = case.repo.dump()
    mutation = (
        "UPDATE publications SET delivery_json=json_set(delivery_json,'$.bill_json',"
        "json_set(json_extract(delivery_json,'$.bill_json'),'$.analysis.analysis.summary','Changed'))"
        f" WHERE id={pub.id};"
    )

    with pytest.raises(ValueError, match="invalid_content_history"):
        case.repo.restore(before + "\n" + mutation)

    assert case.repo.dump() == before


@pytest.mark.parametrize("fault", ["source", "binding"])
def test_doctor_omits_compact_primary_refs_when_evidence_is_invalid_or_unbound(
    case: Case, tmp_path: Path, fault: str
) -> None:
    pub, version = store_marked_card(case)
    reference = version.card_primary_evidence
    assert pub.id is not None and reference is not None, (
        "the fixture supplies its exact original card evidence"
    )
    target = tmp_path / "checkpoint.db"
    with closing(sqlite3.connect(case.path)) as source, closing(sqlite3.connect(target)) as copy:
        source.backup(copy)
        if fault == "source":
            copy.execute(
                "DELETE FROM source_observations WHERE observation_id=?",
                (reference.source_observation_id,),
            )
        else:
            copy.execute("DELETE FROM publication_content WHERE publication_id=?", (pub.id,))
        copy.commit()
    before = target.read_bytes()

    report = audit(target, publication_id=pub.id)

    assert report["status"] == ("failed" if fault == "source" else "recorded")
    assert "card_primary_evidence" not in report["histories"][0]["versions"][0]
    assert target.read_bytes() == before


def compact_revision_candidate(
    case: Case,
    root: Publication,
    previous: ContentVersion,
    previous_id: int,
    bill: Bill,
    edited_at: datetime,
    rendered_text: str,
) -> tuple[int, ContentVersion]:
    identity = case.repo.create_publication(
        Publication(
            term=root.term,
            number=root.number,
            kind=PublicationKind.MESSAGE_PART,
            status=PublicationStatus.QUEUED,
            channel_id=root.channel_id,
            created_at=edited_at,
            ref=f"card/{root.id}/{'a' * 64}-{previous_id}/1",
            delivery=DeliveryPlan(
                bill_json=continuation_snapshot(bill),
                rendered_text=rendered_text,
                revision_of=root.id,
                edit_message_id=10,
                continuation_parent=root.id,
                continuation_index=1,
                reply_to=10,
            ),
        )
    )
    revision = case.repo.publication_by_id(identity)
    assert revision is not None, "the fixture stores a physical revision slot"
    prepared = prepare_publication_content(
        case.repo,
        FixedClock(edited_at),
        revision,
        formatter_version="telegram-format-v2",
        language="ru",
        card_history_parent=previous,
    )
    assert prepared is not None, "the fixture uses the compatible compact edit path"
    plain, snapshots = prepared
    for snapshot in snapshots:
        case.repo.save_content_snapshot(snapshot)
    return identity, plain


def test_first_compact_revision_can_bind_the_same_paid_input_after_cleanup(
    case: Case, tmp_path: Path
) -> None:
    root, previous = store_marked_card(case)
    assert root.id is not None and root.delivery is not None, "the fixture stores the original"
    reference = previous.card_primary_evidence
    assert reference is not None, "the fixture stores the exact original evidence"
    bill = Bill.model_validate_json(root.delivery.bill_json)
    record = bill.analysis
    assert record is not None, "the original render has its exact paid record"
    case.repo.mark_publication(root.id, PublicationStatus.SENT, message_id=10, sent_at=NOW)
    edited_at = NOW + timedelta(days=1)
    edited_id, plain = compact_revision_candidate(
        case, root, previous, root.id, bill, edited_at, "Revised metadata"
    )
    with pytest.raises(ValueError, match="exact compact card profile"):
        case.repo.save_content_version(
            plain.model_copy(update={"card_primary_evidence": reference})
        )
    edited_record = record.model_copy(
        update={"analysis": record.analysis.model_copy(update={"summary": "Another paid answer"})}
    )
    for candidate, reason in (
        (
            reference.model_copy(
                update={"revision_analysis_sha256": primary_record_sha256(edited_record)}
            ),
            "card_revision_primary_result_mismatch",
        ),
        (
            reference.model_copy(
                update={
                    "revision_analysis_sha256": primary_record_sha256(record),
                    "projection_application_id": reference.projection_application_id + 1,
                }
            ),
            "card_revision_primary_predecessor",
        ),
    ):
        with pytest.raises(ValueError, match=reason):
            case.repo.save_content_version(
                plain.model_copy(update={"card_primary_evidence": candidate})
            )
    retained = plain.model_copy(
        update={
            "card_primary_evidence": reference.model_copy(
                update={"revision_analysis_sha256": primary_record_sha256(record)}
            )
        }
    )
    case.repo.save_content_version(retained)
    case.repo.bind_publication_content(edited_id, retained.content_id)
    case.repo.mark_publication(edited_id, PublicationStatus.SENT, message_id=10, sent_at=edited_at)
    case.repo.prune_history(
        before=edited_at + timedelta(days=1), memo_before=NOW - timedelta(days=89)
    )
    sql = case.repo.dump()
    case.repo.restore(sql)
    assert case.repo.dump() == sql
    assert case.repo.get_publication_content(edited_id) == retained
    target = tmp_path / "revision-checkpoint.db"
    with closing(SqliteBillRepository(target)) as copy:
        copy.restore(sql)
    result = audit(target, publication_id=root.id)
    assert result["status"] == "recorded" and not result["findings"]
    assert result["histories"][0]["current_content_id"] == retained.content_id
    marked = retained.card_primary_evidence
    assert marked is not None, "the prospective revision has a persisted witness"
    assert result["histories"][0]["versions"][1]["card_primary_evidence"] == marked.model_dump(
        mode="json"
    )


def test_later_compact_revision_requires_the_same_confirmed_predecessor(case: Case) -> None:
    root, original = store_marked_card(case)
    assert root.id is not None and root.delivery is not None, "the original root is stored"
    bill = Bill.model_validate_json(root.delivery.bill_json)
    assert bill.analysis is not None, "the original has an exact paid record"
    reference = original.card_primary_evidence
    assert reference is not None, "the original binds a paid result"
    reference = reference.model_copy(
        update={"revision_analysis_sha256": primary_record_sha256(bill.analysis)}
    )
    case.repo.mark_publication(root.id, PublicationStatus.SENT, message_id=10, sent_at=NOW)
    first_at = NOW + timedelta(days=1)
    first_id, first = compact_revision_candidate(
        case, root, original, root.id, bill, first_at, "First edit"
    )
    first = first.model_copy(update={"card_primary_evidence": reference})
    case.repo.save_content_version(first)
    case.repo.bind_publication_content(first_id, first.content_id)
    case.repo.mark_publication(first_id, PublicationStatus.SENT, message_id=10, sent_at=first_at)
    second_at = first_at + timedelta(days=1)
    second_id, second = compact_revision_candidate(
        case, root, first, first_id, bill, second_at, "Second edit"
    )
    second = second.model_copy(update={"card_primary_evidence": reference})
    case.execute("UPDATE publications SET status='unknown' WHERE id=?", (first_id,))
    with pytest.raises(ValueError, match="card_revision_primary_predecessor"):
        case.repo.save_content_version(second)
    case.execute("UPDATE publications SET status='sent' WHERE id=?", (first_id,))
    foreign = reference.model_copy(
        update={"projection_application_id": 1 + reference.projection_application_id}
    )
    for candidate in (
        second.model_copy(update={"card_primary_evidence": foreign}),
        second.model_copy(update={"recorded_at": NOW}),
    ):
        with pytest.raises(ValueError, match="card_revision_primary_predecessor"):
            case.repo.save_content_version(candidate)
    case.repo.save_content_version(second)
    case.repo.bind_publication_content(second_id, second.content_id)
    case.repo.mark_publication(second_id, PublicationStatus.SENT, message_id=10, sent_at=second_at)
    case.repo.prune_history(
        before=second_at + timedelta(days=1), memo_before=NOW - timedelta(days=89)
    )
    sql = case.repo.dump()
    case.repo.restore(sql)
    assert case.repo.dump() == sql
    assert case.repo.get_publication_content(second_id) == second

from datetime import datetime, timedelta

import pytest

from lexinform.input_receipts import primary_input_receipt
from lexinform.models import (
    AnalysisRecord,
    ArtifactMetadata,
    BatchIntent,
    Bill,
    ComputationInputBinding,
    ComputationResult,
    DocumentSelectionRecord,
    LlmBatch,
    LlmBatchItem,
    PublicationStatus,
    ResultApplication,
    SelectionArtifactReference,
    SelectionExtractionReference,
    computation_id,
)
from lexinform.models.content import CardPrimaryEvidence
from lexinform.primary_projections import primary_record_sha256
from tests.unit.test_card_primary_evidence_reader import (
    compact_revision_candidate,
    store_marked_card,
)
from tests.unit.test_content_sources import Case
from tests.unit.test_content_sources import case as case
from tests.unit.test_input_receipts import NOW, SOURCE, meta, request, selection
from tests.unit.test_physical_extraction import docx, extractor


def changed_paid_card(case: Case, paid_at: datetime) -> tuple[Bill, CardPrimaryEvidence]:
    repo = case.repo
    text = SOURCE.replace("300 zł", "400 zł")
    raw = docx(text)
    observed = extractor().extract_document_with_provenance(raw)
    artifact = ArtifactMetadata(
        sha256=observed.result.artifact_sha256, byte_size=len(raw), recorded_at=paid_at
    )
    draft = selection(text)
    part = draft.parts[0].model_copy(update={"sha256": artifact.sha256, "text_end": len(text)})
    selected = draft.model_copy(update={"parts": (part,)})
    retained = DocumentSelectionRecord.from_selection(selected)
    observation = case.observation.model_copy(
        update={"selection_id": retained.selection_id, "recorded_at": paid_at}
    )
    repo.save_artifact_metadata(artifact)
    repo.save_extraction_result(observed.result, recorded_at=paid_at)
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
            extraction_id=observed.result.result_id,
            artifact_sha256=artifact.sha256,
        )
    )
    frozen = request(text).model_copy(update={"custom_id": "new-card-input"})
    receipt = primary_input_receipt(
        frozen,
        selected,
        projection=None,
        known_selection=retained,
        body=artifact,
        extraction=observed.result,
    )
    binding = ComputationInputBinding(term=10, number="3039", reuse_key="new-card-input")
    intent = BatchIntent.from_request(
        request=frozen,
        meta=meta(receipt).model_copy(
            update={
                "source_url": part.url,
                "selection": retained.selection,
                "generation": 4,
                "text": text,
                "memo_key": binding.reuse_key,
            }
        ),
        provider="openai",
        created_at=paid_at,
    )
    batch_id = "new-card-evidence"
    item = LlmBatchItem.from_intent(batch_id, intent)
    repo.save_batch_intent(intent)
    repo.save_llm_batch(
        LlmBatch(
            batch_id=batch_id,
            provider="openai",
            call_kind="analysis",
            submitted_at=paid_at,
            status="ended",
            request_count=1,
            estimated_cost_usd=0.01,
        ),
        [item],
    )
    repo.delete_batch_intents([item.custom_id])
    repo.mark_llm_batch_item_consumed(batch_id, item.custom_id, consumed_at=paid_at)
    repo.mark_batch_item_accounted(batch_id, item.custom_id, at=paid_at)
    repo.mark_llm_batch_forgotten(batch_id, forgotten_at=paid_at)
    original = case.paid.record
    assert isinstance(original, AnalysisRecord), "the fixture uses a typed primary result"
    answer = original.model_copy(
        update={
            "computation_id": computation_id("openai", batch_id, item.custom_id),
            "request_sha256": frozen.payload_sha256,
            "created_at": paid_at,
            "analysis": original.analysis.model_copy(
                update={"summary": "Now the amount is 400 PLN."}
            ),
        }
    )
    paid = ComputationResult.model_validate(
        {
            **case.paid.model_dump(),
            "batch_id": batch_id,
            "custom_id": item.custom_id,
            "generation": 4,
            "request_sha256": frozen.payload_sha256,
            "collected_at": paid_at,
            "input_binding": binding,
            "record": answer,
        }
    )
    repo.save_computation_result(paid)
    collection = ResultApplication(
        result_id=paid.result_id,
        term=10,
        number="3039",
        generation=4,
        outcome="ready",
        reason="current_generation",
        observed_generation=4,
        observed_status=None,
        created_at=paid_at,
    )
    repo.save_result_application(collection)
    repo.save_result_application(
        collection.model_copy(
            update={
                "phase": "projection",
                "reason": "batch_result",
                "outcome": "applied",
                "projected_generation": 5,
            }
        )
    )
    bill = case.bill.model_copy(update={"analysis": answer, "analysis_generation": 5})
    reference = repo.find_card_primary_evidence(bill)
    assert reference is not None, "the new fixture has a distinct paid projection and exact input"
    return bill, reference.model_copy(
        update={
            "revision_analysis_sha256": primary_record_sha256(answer),
            "revision_projection": "current",
        }
    )


@pytest.mark.parametrize("projection_after_predecessor", [False, True])
def test_reader_requires_current_paid_projection_and_its_confirmed_predecessor(
    case: Case, projection_after_predecessor: bool
) -> None:
    root, previous = store_marked_card(case)
    assert root.id is not None, "the fixture retains an original physical card"
    sent_at = NOW + timedelta(days=1)
    case.repo.mark_publication(root.id, PublicationStatus.SENT, message_id=10, sent_at=sent_at)
    paid_at = NOW + timedelta(days=2) if projection_after_predecessor else NOW
    bill, reference = changed_paid_card(case, paid_at)
    at = NOW + timedelta(days=3)
    identity, plain = compact_revision_candidate(
        case, root, previous, root.id, bill, at, "New edition"
    )
    unchanged = case.repo.dump()
    marked = plain.model_copy(update={"card_primary_evidence": reference})
    if not projection_after_predecessor:
        with pytest.raises(ValueError, match="card_primary_chronology"):
            case.repo.save_content_version(marked)
        assert case.repo.dump() == unchanged
        return
    for wrong in (
        reference.model_copy(update={"revision_analysis_sha256": "0" * 64}),
        reference.model_copy(update={"source_observation_id": "source-observation-v1:" + "0" * 64}),
    ):
        with pytest.raises(ValueError):
            case.repo.save_content_version(
                plain.model_copy(update={"card_primary_evidence": wrong})
            )
        assert case.repo.dump() == unchanged
    case.repo.save_content_version(marked)
    case.repo.bind_publication_content(identity, marked.content_id)
    case.repo.mark_publication(identity, PublicationStatus.SENT, message_id=10, sent_at=at)
    case.repo.prune_history(before=at + timedelta(days=1), memo_before=NOW)
    sql = case.repo.dump()
    case.repo.restore(sql)
    assert case.repo.dump() == sql
    assert case.repo.get_publication_content(root.id) == previous
    assert case.repo.get_publication_content(identity) == marked

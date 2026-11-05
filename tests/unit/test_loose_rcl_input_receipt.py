import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.computation_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AnalysisQuestion,
    ArtifactMetadata,
    BatchIntent,
    BatchItemMeta,
    BatchRequest,
    DocumentPart,
    DocumentSelection,
    DocumentSelectionRecord,
    LlmBatch,
    LlmBatchItem,
    SelectionArtifactReference,
    SelectionExtractionReference,
    SourceObservation,
    TextDocument,
)
from lexinform.models.input_receipts import (
    LooseRclInputPart,
    LooseRclInputReceipt,
    input_map_sha256,
    input_text_sha256,
    primary_context_sha256,
)
from lexinform.prepared_input import prepared_text_input
from lexinform.sections import PAGE_BREAK
from lexinform.text_projection import SourcePiece, project_pieces
from tests.harness import summary
from tests.unit.test_input_receipts import NOW, SOURCE, meta, request
from tests.unit.test_physical_extraction import docx, extractor


def known_inputs(repo: SqliteBillRepository) -> tuple[BatchIntent, LooseRclInputReceipt]:
    repo.migrate()
    repo.upsert_summary(summary("3039", "Controlled RCL input"), now=NOW)
    parts = []
    extractions = []
    for role, text in (("bill", SOURCE * 20), ("normative_annex", "Załącznik: 400 zł." * 20)):
        data = docx(text)
        observed = extractor().extract_document_with_provenance(data)
        result = observed.result
        repo.save_artifact_metadata(
            ArtifactMetadata(sha256=result.artifact_sha256, byte_size=len(data), recorded_at=NOW)
        )
        repo.save_extraction_result(result, recorded_at=NOW)
        extractions.append(result)
        parts.append(
            DocumentPart(
                role=role,
                url=f"https://example.test/{role}.docx",
                sha256=result.artifact_sha256,
                text=observed.document.text,
                text_end=result.output_chars,
                text_sha256=result.output_text_sha256,
            )
        )
    selection = DocumentSelection(parts=tuple(parts))
    known = DocumentSelectionRecord.from_selection(selection)
    repo.save_source_observation(
        known,
        SourceObservation(
            term=10,
            number="3039",
            purpose="analysis",
            document=TextDocument(url=parts[0].url, kind="rcl", normative_urls=(parts[1].url,)),
            selection_id=known.selection_id,
            recorded_at=NOW,
        ),
    )
    for index, extraction in enumerate(extractions):
        repo.save_selection_artifact(
            SelectionArtifactReference(
                selection_id=known.selection_id,
                part_scope="parts",
                part_index=index,
                artifact_sha256=extraction.artifact_sha256,
            )
        )
        repo.save_selection_extraction(
            SelectionExtractionReference(
                selection_id=known.selection_id,
                extraction_id=extraction.result_id,
                artifact_sha256=extraction.artifact_sha256,
            )
        )
    source = PAGE_BREAK.join(part.text for part in parts)
    projection = project_pieces(
        source,
        (
            SourcePiece(parts[0].text[:9], 0),
            SourcePiece(PAGE_BREAK),
            SourcePiece(parts[1].text[:16], len(parts[0].text) + 1),
        ),
    )
    frozen_question = request().question
    assert isinstance(frozen_question, AnalysisQuestion), "the fixture uses primary analysis"
    ctx = frozen_question.ctx.model_copy(update={"text": projection.text, "source_kind": "rcl"})
    frozen = BatchRequest(
        custom_id="loose-rcl",
        term=10,
        number="3039",
        question=AnalysisQuestion(kind="analysis", ctx=ctx),
    ).freeze(
        payload_json=ctx.model_dump_json(),
        model="gpt-6.1-sol",
        prompt_version="pinned",
        estimated_cost_usd=0.01,
    )
    prepared = prepared_text_input(selection, ctx.text, projection=projection)
    assert frozen.payload_sha256 is not None, "the fixture retains the exact payload identity"
    receipt = LooseRclInputReceipt(
        source_selection_id=known.selection_id,
        source_parts=tuple(
            LooseRclInputPart(**part.model_dump(), extraction_id=extraction.result_id)
            for part, extraction in zip(prepared.parts, extractions, strict=True)
        ),
        context_sha256=primary_context_sha256(ctx),
        request_sha256=frozen.payload_sha256,
        text_sha256=input_text_sha256(ctx.text),
        text_chars=len(ctx.text),
        full_map_sha256=input_map_sha256(prepared.segments),
        segments=prepared.segments,
    )
    metadata = meta().model_copy(
        update={
            "selection": known.selection,
            "source_kind": "rcl",
            "source_url": parts[0].url,
            "input_chars": len(ctx.text),
            "prepared_input_receipt": receipt,
        }
    )
    return BatchIntent.from_request(
        request=frozen, meta=metadata, provider="openai", created_at=NOW
    ), receipt


def test_loose_rcl_receipt_round_trips_queued_and_submitted_metadata(tmp_path: Path) -> None:
    path = tmp_path / "rcl.db"
    with closing(SqliteBillRepository(path)) as repo:
        intent, receipt = known_inputs(repo)
        repo.save_batch_intent(intent)
        queued = repo.dump()
        repo.restore(queued)
        assert repo.list_queued_batch_intents() == [intent] and repo.dump() == queued
        batch = LlmBatch(
            batch_id="rcl-reader",
            provider="openai",
            call_kind="analysis",
            submitted_at=NOW,
            status="submitted",
            request_count=1,
            estimated_cost_usd=0.01,
        )
        item = LlmBatchItem.from_intent(batch.batch_id, intent)
        repo.save_llm_batch(batch, [item])
        repo.delete_batch_intents([intent.request.custom_id])
        submitted = repo.dump()
        repo.restore(submitted)
        assert repo.list_llm_batch_items(batch.batch_id) == [item] and repo.dump() == submitted
        assert receipt.segments[0].part_index == 0 and receipt.segments[2].part_index == 1
        assert receipt.segments[1].origin == "generated"
        assert SOURCE not in receipt.model_dump_json()
    observed = audit(path)
    assert observed["status"] == "recorded" and observed["summary"]["input_receipts"] == 1


def test_loose_rcl_map_rejects_cross_part_escape_even_with_recomputed_hash() -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        _, receipt = known_inputs(repo)
    segment = receipt.segments[-1]
    end = receipt.source_parts[1].chars + 1
    invalid = segment.model_copy(
        update={"source_start": end - (segment.end - segment.start), "source_end": end}
    )
    segments = (*receipt.segments[:-1], invalid)

    with pytest.raises(ValueError, match="escapes its referenced part"):
        LooseRclInputReceipt.model_validate(
            {
                **receipt.model_dump(),
                "segments": segments,
                "full_map_sha256": input_map_sha256(segments),
            }
        )


def test_loose_rcl_receipt_rejects_swapped_parents_on_save_and_restore(tmp_path: Path) -> None:
    path = tmp_path / "rcl.db"
    with closing(SqliteBillRepository(path)) as repo:
        intent, receipt = known_inputs(repo)
        assert isinstance(intent.meta, BatchItemMeta), "the fixture uses primary metadata"
        first, second = receipt.source_parts
        swapped = receipt.model_copy(
            update={
                "source_parts": (
                    first.model_copy(update={"extraction_id": second.extraction_id}),
                    second.model_copy(update={"extraction_id": first.extraction_id}),
                )
            }
        )
        bad_meta = intent.meta.model_copy(update={"prepared_input_receipt": swapped})
        bad_intent = intent.model_copy(update={"meta": bad_meta})
        with pytest.raises(ValueError, match="input_receipt_extraction_mismatch"):
            repo.save_batch_intent(bad_intent)
        assert not repo.list_queued_batch_intents()
        repo.save_batch_intent(intent)
        before = repo.dump()
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "UPDATE llm_batch_intents SET meta_json=?", (bad_meta.model_dump_json(),)
            )
        bad = repo.dump()
        repo.restore(before)
        with pytest.raises(ValueError, match="invalid_input_intent_snapshot"):
            repo.restore(bad)
        assert repo.dump() == before

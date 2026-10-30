import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
from tools.computation_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.input_receipts import primary_input_receipt
from lexinform.models import (
    ArtifactMetadata,
    BatchIntent,
    BatchItemMeta,
    BatchRequest,
    BatchResult,
    BillStatus,
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
from lexinform.text_projection import SourcePiece, project_pieces
from tests.harness import summary
from tests.unit.test_input_receipts import NOW, SOURCE, meta, request, selection
from tests.unit.test_physical_extraction import docx, extractor


def source(path: Path, *, parents: bool = False) -> tuple[SqliteBillRepository, BatchIntent]:
    repo = SqliteBillRepository(path)
    repo.migrate()
    repo.upsert_summary(summary("3039", "Controlled input snapshot"), now=NOW)
    selected = selection()
    frozen = request()
    known = None
    body = None
    extraction = None
    if parents:
        data = docx(SOURCE * 20)
        observed = extractor().extract_document_with_provenance(data)
        text = observed.document.text
        body = ArtifactMetadata(
            sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data), recorded_at=NOW
        )
        selected = DocumentSelection(
            parts=(
                DocumentPart(
                    role="bill",
                    text=text,
                    text_end=len(text),
                    sha256=body.sha256,
                    text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                ),
            )
        )
        frozen = request(text)
        known = DocumentSelectionRecord.from_selection(selected)
        extraction = observed.result
        repo.save_artifact_metadata(body)
        repo.save_extraction_result(extraction, recorded_at=NOW)
        repo.save_source_observation(
            known,
            SourceObservation(
                term=10,
                number="3039",
                purpose="analysis",
                document=TextDocument(url="https://example.test/input.docx", kind="print"),
                selection_id=known.selection_id,
                recorded_at=NOW,
            ),
        )
        repo.save_selection_artifact(
            SelectionArtifactReference(
                selection_id=known.selection_id,
                part_scope="parts",
                part_index=0,
                artifact_sha256=body.sha256,
            )
        )
        repo.save_selection_extraction(
            SelectionExtractionReference(
                selection_id=known.selection_id,
                extraction_id=extraction.result_id,
                artifact_sha256=body.sha256,
            )
        )
    text = selected.parts[0].text
    receipt = primary_input_receipt(
        frozen,
        selected,
        projection=project_pieces(text, (SourcePiece(text, 0),)),
        known_selection=known,
        body=body,
        extraction=extraction,
    )
    metadata = meta().model_copy(
        update={"input_chars": len(text), "text": text, "prepared_input_receipt": receipt}
    )
    intent = BatchIntent.from_request(
        request=frozen, meta=metadata, provider="openai", created_at=NOW
    )
    repo.save_batch_intent(intent)
    return repo, intent


def corrupt(script: str, field: str, value: str, replacement: str = "f" * 64) -> str:
    needle = f'"{field}":"{value}"'
    assert script.count(needle) == 1, "the controlled source has one exact field occurrence"
    return script.replace(needle, f'"{field}":"{replacement}"')


@pytest.mark.parametrize("field", ["full_map_sha256", "payload_sha256", "context_sha256"])
def test_restore_refuses_corrupt_receipt_or_request_before_touching_source_or_target(
    tmp_path: Path,
    field: str,
) -> None:
    with (
        closing(source(tmp_path / "source.db")[0]) as original,
        closing(SqliteBillRepository(tmp_path / "target.db")) as target,
    ):
        target.migrate()
        target.upsert_summary(summary("3999", "Keep this target"), now=NOW)
        before = target.dump()
        script = original.dump()
        intent = original.list_queued_batch_intents()[0]
        assert (
            isinstance(intent.meta, BatchItemMeta)
            and intent.meta.prepared_input_receipt is not None
        ), "the fixture has a prospective primary receipt"
        receipt = intent.meta.prepared_input_receipt
        value = (
            intent.request.payload_sha256
            if field == "payload_sha256"
            else receipt.full_map_sha256
            if field == "full_map_sha256"
            else receipt.context_sha256
        )
        assert value is not None, "the selected controlled field is known"
        with pytest.raises(ValueError, match="invalid_input_intent_snapshot"):
            target.restore(corrupt(script, field, value))
        assert target.dump() == before and original.dump() == script


def test_known_frozen_request_is_checked_without_receipt_and_legacy_unknown_hash_stays_unknown(
    tmp_path: Path,
) -> None:
    with closing(source(tmp_path / "source.db")[0]) as repo:
        original = repo.list_queued_batch_intents()[0]
        assert isinstance(original.meta, BatchItemMeta), "source fixture queues primary metadata"
        repo.delete_batch_intents([original.request.custom_id])
        plain = original.model_copy(
            update={"meta": original.meta.model_copy(update={"prepared_input_receipt": None})}
        )
        repo.save_batch_intent(plain)
        valid = repo.dump()
        checksum = original.request.payload_sha256
        assert checksum is not None, "the controlled request is frozen"
        with pytest.raises(ValueError, match="invalid_input_intent_snapshot"):
            repo.restore(corrupt(valid, "payload_sha256", checksum))
        assert repo.dump() == valid
        legacy = plain.request.model_dump(mode="json")
        question = legacy.pop("question")
        assert isinstance(question, dict), "BatchRequest serializes its tagged question"
        legacy.update(call_kind=question["kind"], ctx=question["ctx"], payload_sha256=None)
        old = BatchRequest.model_validate(legacy)
        repo.delete_batch_intents([original.request.custom_id])
        repo.save_batch_intent(plain.model_copy(update={"request": old}))
        dump = repo.dump()
        repo.restore(dump)
        assert (
            repo.dump() == dump
            and repo.list_queued_batch_intents()[0].request.payload_sha256 is None
        )


@pytest.mark.parametrize("field", ["source_selection_id", "artifact_sha256", "extraction_id"])
def test_known_missing_parent_reference_is_refused_by_restore_and_doctor(
    tmp_path: Path, field: str
) -> None:
    path = tmp_path / "source.db"
    with closing(source(path, parents=True)[0]) as repo:
        intent = repo.list_queued_batch_intents()[0]
        assert (
            isinstance(intent.meta, BatchItemMeta)
            and intent.meta.prepared_input_receipt is not None
        ), "the actual controlled parents are known"
        receipt = intent.meta.prepared_input_receipt
        value = getattr(receipt, field)
        assert isinstance(value, str), "the selected known parent reference is present"
        script = repo.dump()
        needle = f'"{field}":"{value}"'
        replacement = (
            "selection-v1:"
            if field == "source_selection_id"
            else "extraction-v1:"
            if field == "extraction_id"
            else ""
        ) + "e" * 64
        corrupt_meta = intent.meta.model_copy(
            update={"prepared_input_receipt": receipt.model_copy(update={field: replacement})}
        )
        repo.delete_batch_intents([intent.request.custom_id])
        repo.save_batch_intent(intent.model_copy(update={"meta": corrupt_meta}))
        bad = repo.dump()
        with pytest.raises(ValueError, match="invalid_input_intent_snapshot"):
            repo.restore(bad)
        assert repo.dump() == bad and needle in script
    diagnosed = audit(path)
    assert diagnosed["status"] == "failed"
    assert any(item["code"] == "invalid_input_intent_snapshot" for item in diagnosed["findings"])
    assert SOURCE not in json.dumps(diagnosed)


def test_actual_native_body_selection_and_extraction_roundtrip_as_known_parents(
    tmp_path: Path,
) -> None:
    path = tmp_path / "known-parents.db"
    with closing(source(path, parents=True)[0]) as repo:
        queued = repo.list_queued_batch_intents()
        original = repo.dump()
        repo.restore(original)
        assert repo.list_queued_batch_intents() == queued and repo.dump() == original
    checked = audit(path)
    assert checked["status"] == "recorded" and checked["summary"]["input_receipts"] == 1


def test_submitted_consumed_and_cleaned_item_preserves_receipt_without_inventing_context(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.db"
    with closing(source(path)[0]) as repo:
        intent = repo.list_queued_batch_intents()[0]
        batch = LlmBatch(
            batch_id="controlled-batch",
            provider="openai",
            call_kind="analysis",
            submitted_at=NOW,
            status="submitted",
            request_count=1,
            estimated_cost_usd=0,
        )
        repo.save_llm_batch(batch, [LlmBatchItem.from_intent(batch.batch_id, intent)])
        repo.delete_batch_intents([intent.request.custom_id])
        repo.mark_llm_batch_item_consumed(
            batch.batch_id,
            intent.request.custom_id,
            consumed_at=NOW,
            result=BatchResult(custom_id=intent.request.custom_id),
        )
        repo.mark_batch_item_accounted(batch.batch_id, intent.request.custom_id, at=NOW)
        repo.mark_llm_batch_collected(batch.batch_id, completed_at=NOW)
        repo.mark_llm_batch_forgotten(batch.batch_id, forgotten_at=NOW)
        repo.set_status(10, "3039", BillStatus.ANALYZED)
        repo.prune_history(before=NOW + timedelta(days=365), memo_before=NOW + timedelta(days=365))
        with closing(sqlite3.connect(path)) as inspection:
            row = inspection.execute("SELECT meta_json FROM llm_batch_items").fetchone()
        assert row is not None, "consumed cleanup retains the original item metadata row"
        retained = BatchItemMeta.model_validate_json(row[0])
        assert not retained.text and retained.located is None
        assert retained.prepared_input_receipt is not None
        script = repo.dump()
        repo.restore(script)
        assert repo.list_llm_batch_items(batch.batch_id) == [] and repo.dump() == script
    checked = audit(path)
    assert checked["status"] == "recorded" and checked["summary"]["input_receipts"] == 1


@pytest.mark.parametrize("parent", ["selection", "extraction"])
def test_existing_foreign_parent_cannot_borrow_another_saved_source_binding(
    tmp_path: Path, parent: str
) -> None:
    with closing(source(tmp_path / "source.db", parents=True)[0]) as repo:
        intent = repo.list_queued_batch_intents()[0]
        assert (
            isinstance(intent.meta, BatchItemMeta)
            and intent.meta.prepared_input_receipt is not None
        ), "the fixture keeps exact known parents"
        receipt = intent.meta.prepared_input_receipt
        if parent == "selection":
            other = DocumentSelectionRecord.from_selection(
                selection("Different actual cached source")
            )
            repo.save_source_observation(
                other,
                SourceObservation(
                    term=10,
                    number="3039",
                    purpose="analysis",
                    document=TextDocument(url="https://example.test/other", kind="print"),
                    selection_id=other.selection_id,
                    recorded_at=NOW,
                ),
            )
            field, old, replacement = (
                "source_selection_id",
                receipt.source_selection_id,
                other.selection_id,
            )
        else:
            original = repo.get_extraction_result(receipt.extraction_id or "")
            assert original is not None, "the fixture stores the referenced actual extraction"
            different = original.model_copy(update={"package_policy_source_sha256": "e" * 64})
            repo.save_extraction_result(different, recorded_at=NOW)
            field, old, replacement = "extraction_id", receipt.extraction_id, different.result_id
        assert old is not None, "the selected known reference was originally present"
        before = repo.dump()
        with pytest.raises(ValueError, match="invalid_input_intent_snapshot"):
            repo.restore(corrupt(before, field, old, replacement))
        assert repo.dump() == before


def test_item_request_checksum_is_checked_without_rebuilding_erased_or_receiver_context(
    tmp_path: Path,
) -> None:
    with closing(source(tmp_path / "source.db")[0]) as repo:
        intent = repo.list_queued_batch_intents()[0]
        batch = LlmBatch(
            batch_id="controlled-batch",
            provider="openai",
            call_kind="analysis",
            submitted_at=NOW,
            status="submitted",
            request_count=1,
            estimated_cost_usd=0,
        )
        item = LlmBatchItem.from_intent(batch.batch_id, intent)
        assert isinstance(item.meta, BatchItemMeta), "the controlled item retains primary metadata"
        changed = item.model_copy(
            update={
                "term": 11,
                "number": "other-receiver",
                "meta": item.meta.model_copy(
                    update={"text": "Different retained non-provider source"}
                ),
            }
        )
        repo.save_llm_batch(batch, [changed])
        repo.delete_batch_intents([intent.request.custom_id])
        valid = repo.dump()
        repo.restore(valid)
        assert repo.list_llm_batch_items(batch.batch_id) == [changed]
        assert item.meta.request_sha256 is not None, (
            "submitted items preserve their actual known request checksum"
        )
        with pytest.raises(ValueError, match="invalid_input_item_snapshot"):
            needle = f'"request_sha256":"{item.meta.request_sha256}"'
            assert valid.count(needle) == 2, (
                "metadata and its child receipt retain the same known request checksum"
            )
            repo.restore(valid.replace(needle, '"request_sha256":"' + "f" * 64 + '"', 1))
        assert repo.dump() == valid


@pytest.mark.parametrize("custom_id,provider", [("different", "openai"), ("input", "unsupported")])
def test_known_request_requires_its_real_row_key_and_closed_provider(
    tmp_path: Path, custom_id: str, provider: str
) -> None:
    with closing(source(tmp_path / "source.db")[0]) as repo:
        valid = repo.dump()
        old = "INSERT INTO \"llm_batch_intents\" VALUES('input','openai',"
        assert valid.count(old) == 1, "the controlled fixture owns one indexed intent row"
        wrong = valid.replace(
            old, f"INSERT INTO \"llm_batch_intents\" VALUES('{custom_id}','{provider}',"
        )
        with pytest.raises(ValueError, match="invalid_input_intent_snapshot"):
            repo.restore(wrong)
        assert repo.dump() == valid

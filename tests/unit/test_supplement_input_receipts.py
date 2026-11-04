import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from tools.computation_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.input_receipts import primary_input_receipt
from lexinform.models import (
    AnalysisQuestion,
    BatchIntent,
    BatchItemMeta,
    BatchRequest,
    BatchResult,
    BillStatus,
    DigestItemMeta,
    LlmBatch,
    LlmBatchItem,
    SupplementContext,
    SupplementInputReceipt,
    SupplementQuestion,
)
from lexinform.models.batch import batch_item_meta
from lexinform.models.input_receipts import MAX_INPUT_RECEIPT_BYTES, supplement_context_sha256
from lexinform.text_projection import SourcePiece, project_pieces
from tests.unit.test_input_receipt_validation import source
from tests.unit.test_input_receipts import NOW, SOURCE, request, selection


def supplement_intent(primary: BatchIntent) -> BatchIntent:
    assert isinstance(primary.meta, BatchItemMeta), "the controlled source queues primary metadata"
    old = primary.meta.prepared_input_receipt
    assert old is not None, "the controlled source retains a mapped primary receipt"
    assert isinstance(primary.request.question, AnalysisQuestion), (
        "the controlled question is primary"
    )
    ctx = SupplementContext(
        number=primary.request.number,
        title="Projekt ustawy — 😀",
        document_title="Uzupełnienie",
        source_kind="print",
        text=primary.request.question.ctx.text,
        truncated=False,
        previous_summary="Предыдущий контекст",
        previous_key_changes=["Сумма 300 zł", "Дата 2030"],
    )
    frozen = BatchRequest(
        custom_id="supplement-input",
        term=10,
        number=ctx.number,
        question=SupplementQuestion(ctx=ctx),
    ).freeze(
        payload_json=json.dumps({"supplement": ctx.model_dump(mode="json")}),
        model="gpt-6.1-sol",
        prompt_version="frozen-supplement",
        estimated_cost_usd=0.01,
    )
    retained = old.model_dump(mode="json")
    retained.update(
        profile="supplement-single-part-text-v1",
        context_encoding="supplement-context-json-v1",
        context_sha256=hashlib.sha256(
            json.dumps(ctx.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        request_sha256=frozen.payload_sha256,
    )
    receipt = SupplementInputReceipt.model_validate(retained)
    return BatchIntent.from_request(
        request=frozen,
        meta=DigestItemMeta(memo_key="supplement-key", prepared_input_receipt=receipt),
        provider="openai",
        created_at=NOW,
    )


def supplement_source(
    path: Path, *, parents: bool = False
) -> tuple[SqliteBillRepository, BatchIntent]:
    repo, primary = source(path, parents=parents)
    intent = supplement_intent(primary)
    repo.delete_batch_intents([primary.request.custom_id])
    repo.save_batch_intent(intent)
    return repo, intent


def test_primary_and_legacy_digest_serialization_match_fixed_pre_reader_baseline() -> None:
    receipt = primary_input_receipt(
        request(), selection(), projection=project_pieces(SOURCE, (SourcePiece(SOURCE, 0),))
    )
    assert hashlib.sha256(receipt.model_dump_json().encode()).hexdigest() == (
        "0457d730c7ba0e75dc4089c43b72c5cf3e427f0882bc9a18f2642664c0a683cd"
    )
    old = (
        '{"queued_at":null,"memo_key":"frozen-key","delivery_key":"","prompt_version":"",'
        '"source_kind":"print","title":"","compared_with":[],"review_context":null,'
        '"amendment_revision":null,"amendment_source":null,"explanation":null}'
    )
    assert DigestItemMeta(memo_key="frozen-key").model_dump_json() == old
    assert DigestItemMeta.model_validate_json(old).model_dump_json() == old


def test_supplement_reader_identifies_exact_frozen_context_without_retaining_text(
    tmp_path: Path,
) -> None:
    with closing(supplement_source(tmp_path / "source.db")[0]) as repo:
        intent = repo.list_queued_batch_intents()[0]
        assert isinstance(intent.meta, DigestItemMeta), "the fixture queues a supplement"
        receipt = intent.meta.prepared_input_receipt
        assert receipt is not None, "the fixture has a prospective text receipt"
        assert isinstance(intent.request.question, SupplementQuestion), (
            "the receipt has its question"
        )
        assert receipt.context_sha256 == supplement_context_sha256(intent.request.question.ctx)
        assert receipt.physical_origins == receipt.block_origins == "unmapped"
        assert receipt.exclusions == "unknown" and receipt.input_completeness == "not_asserted"
        assert receipt.retention == "complete" and len(receipt.segments) == 1
        encoded = receipt.model_dump_json()
        assert SOURCE not in encoded and "Предыдущий" not in encoded
        assert len(encoded.encode()) <= MAX_INPUT_RECEIPT_BYTES
        assert BatchIntent.model_validate_json(intent.model_dump_json()) == intent
        assert intent.meta.request_sha256 is None
        repo.mark_batch_intents_submitting([intent.request.custom_id])
        submitting = repo.list_submitting_batch_intents()
        assert len(submitting) == 1 and submitting[0].meta == intent.meta


@pytest.mark.parametrize(
    "fault",
    [
        "context",
        "payload",
        "request_sha",
        "text_sha",
        "text_chars",
        "profile",
        "encoding",
        "map",
        "kind",
        "scan_meta",
        "scan_context",
    ],
)
def test_supplement_intent_rejects_mismatched_receipts_without_union_fallback(
    tmp_path: Path, fault: str
) -> None:
    with closing(supplement_source(tmp_path / "source.db")[0]) as repo:
        raw: dict[str, Any] = repo.list_queued_batch_intents()[0].model_dump(mode="json")
    receipt = raw["meta"]["prepared_input_receipt"]
    if fault == "context":
        raw["request"]["question"]["ctx"]["previous_summary"] += " changed"
    elif fault == "payload":
        raw["request"]["payload_json"] += " "
    elif fault == "request_sha":
        raw["meta"]["request_sha256"] = "a" * 64
    elif fault == "text_sha":
        receipt["text_sha256"] = "a" * 64
    elif fault == "text_chars":
        receipt["text_chars"] += 1
    elif fault == "profile":
        receipt["profile"] = "primary-single-part-text-v1"
    elif fault == "encoding":
        receipt["context_encoding"] = "bill-context-json-v1"
    elif fault == "map":
        receipt["segments"][0]["source_end"] -= 1
    elif fault == "kind":
        raw["request"] = request().model_dump(mode="json")
    elif fault == "scan_context":
        raw["request"]["question"]["ctx"]["scan"] = {
            "data": "ZmlsZQ==",
            "pages": 1,
            "of_pages": 1,
            "sha256": "a" * 64,
        }
    else:
        raw["meta"]["scan_input"] = {}
    with pytest.raises(ValidationError):
        BatchIntent.model_validate(raw)


def test_supplement_unknown_maps_and_prefixes_keep_shared_bounds(tmp_path: Path) -> None:
    with closing(supplement_source(tmp_path / "source.db")[0]) as repo:
        metadata = repo.list_queued_batch_intents()[0].meta
        receipt = metadata.prepared_input_receipt
        assert receipt is not None, "the fixture retains its bounded receipt"
        raw = receipt.model_dump(mode="json")
    unknown = {
        **raw,
        "retention": "unknown",
        "reason": "projection_unavailable",
        "segments": [],
        "segment_count": None,
        "full_map_sha256": None,
    }
    observed = SupplementInputReceipt.model_validate(unknown)
    assert not observed.segments and observed.segment_count is None
    prefix = {**raw, "retention": "prefix", "reason": "segment_limit", "segment_count": 33}
    assert SupplementInputReceipt.model_validate(prefix).retention == "prefix"
    with pytest.raises(ValidationError):
        SupplementInputReceipt.model_validate({**unknown, "segments": raw["segments"]})
    with pytest.raises(ValidationError):
        SupplementInputReceipt.model_validate({**prefix, "segments": raw["segments"] * 33})


@pytest.mark.parametrize("parents", [False, True])
def test_future_supplement_intent_restores_and_doctor_accepts_known_parents(
    tmp_path: Path, parents: bool
) -> None:
    path = tmp_path / "source.db"
    with closing(supplement_source(path, parents=parents)[0]) as repo:
        queued = repo.list_queued_batch_intents()
        script = repo.dump()
        repo.restore(script)
        assert repo.dump() == script and repo.list_queued_batch_intents() == queued
    report = audit(path)
    assert report["status"] == "recorded" and report["summary"]["input_receipts"] == 1


@pytest.mark.parametrize(
    "field",
    [
        "context_sha256",
        "full_map_sha256",
        "source_selection_id",
        "artifact_sha256",
        "extraction_id",
    ],
)
def test_supplement_restore_and_doctor_fail_closed_on_tamper(tmp_path: Path, field: str) -> None:
    path = tmp_path / "source.db"
    with closing(supplement_source(path, parents=True)[0]) as repo:
        intent = repo.list_queued_batch_intents()[0]
        receipt = intent.meta.prepared_input_receipt
        assert receipt is not None, "the controlled supplement retains a mapped receipt"
        value = getattr(receipt, field)
        assert isinstance(value, str), "the controlled parent or checksum is known"
        prefix = value.split(":")[0] + ":" if ":" in value else ""
        bad_receipt = receipt.model_copy(update={field: prefix + "e" * 64})
        metadata = intent.meta.model_copy(update={"prepared_input_receipt": bad_receipt})
        repo.delete_batch_intents([intent.request.custom_id])
        repo.save_batch_intent(intent.model_copy(update={"meta": metadata}))
        bad = repo.dump()
        with pytest.raises(ValueError, match="invalid_input_intent_snapshot"):
            repo.restore(bad)
        assert repo.dump() == bad
    report = audit(path)
    assert report["status"] == "failed"
    assert any(item["code"] == "invalid_input_intent_snapshot" for item in report["findings"])
    assert SOURCE not in json.dumps(report)


def test_submitted_supplement_receipt_survives_consumed_cleanup_without_context(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.db"
    with closing(supplement_source(path)[0]) as repo:
        intent = repo.list_queued_batch_intents()[0]
        batch = LlmBatch(
            batch_id="supplement-batch",
            provider="openai",
            call_kind="supplement",
            submitted_at=NOW,
            status="submitted",
            request_count=1,
            estimated_cost_usd=0,
        )
        item = LlmBatchItem.from_intent(batch.batch_id, intent)
        repo.save_llm_batch(batch, [item])
        repo.delete_batch_intents([intent.request.custom_id])
        repo.mark_llm_batch_item_consumed(
            batch.batch_id,
            item.custom_id,
            consumed_at=NOW,
            result=BatchResult(custom_id=item.custom_id),
        )
        repo.mark_batch_item_accounted(batch.batch_id, item.custom_id, at=NOW)
        repo.mark_llm_batch_collected(batch.batch_id, completed_at=NOW)
        repo.mark_llm_batch_forgotten(batch.batch_id, forgotten_at=NOW)
        repo.set_status(10, "3039", BillStatus.ANALYZED)
        repo.prune_history(before=NOW + timedelta(days=365), memo_before=NOW + timedelta(days=365))
        with closing(sqlite3.connect(path)) as read:
            row = read.execute("SELECT meta_json FROM llm_batch_items").fetchone()
        assert row is not None, "cleanup retains consumed batch metadata"
        kept = DigestItemMeta.model_validate_json(row[0])
        assert kept.prepared_input_receipt == item.meta.prepared_input_receipt
        assert kept.request_sha256 == intent.request.payload_sha256
        script = repo.dump()
        repo.restore(script)
        assert repo.dump() == script
    report = audit(path)
    assert report["status"] == "recorded" and report["summary"]["input_receipts"] == 1


@pytest.mark.parametrize("fault", ["purpose", "request_sha256", "missing_request_sha256", "map"])
def test_consumed_supplement_reader_refuses_wrong_purpose_hash_or_map(
    tmp_path: Path, fault: str
) -> None:
    path = tmp_path / "source.db"
    with closing(supplement_source(path)[0]) as repo:
        intent = repo.list_queued_batch_intents()[0]
        batch = LlmBatch(
            batch_id="future-supplement",
            provider="openai",
            call_kind="supplement",
            submitted_at=NOW,
            status="submitted",
            request_count=1,
            estimated_cost_usd=0,
        )
        item = LlmBatchItem.from_intent(batch.batch_id, intent)
        assert isinstance(item.meta, DigestItemMeta), "the future item keeps supplement metadata"
        receipt = item.meta.prepared_input_receipt
        assert receipt is not None, "the future item has a mapped receipt"
        kind = "name" if fault == "purpose" else "supplement"
        metadata = item.meta
        if fault == "request_sha256":
            metadata = metadata.model_copy(update={"request_sha256": "a" * 64})
        elif fault == "missing_request_sha256":
            metadata = metadata.model_copy(update={"request_sha256": None})
        elif fault == "map":
            metadata = metadata.model_copy(
                update={
                    "prepared_input_receipt": receipt.model_copy(
                        update={"full_map_sha256": "e" * 64}
                    )
                }
            )
        with pytest.raises(ValueError):
            batch_item_meta(kind, metadata.model_dump_json())
        changed = item.model_copy(update={"call_kind": kind, "meta": metadata})
        repo.save_llm_batch(batch, [changed])
        repo.delete_batch_intents([intent.request.custom_id])
        repo.mark_llm_batch_item_consumed(batch.batch_id, item.custom_id, consumed_at=NOW)
        bad = repo.dump()
        with pytest.raises(ValueError, match="invalid_input_item_snapshot"):
            repo.restore(bad)
        assert repo.dump() == bad
    report = audit(path)
    assert report["status"] == "failed"
    assert any(item["code"] == "invalid_input_item_snapshot" for item in report["findings"])

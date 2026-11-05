import hashlib
import json
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from tools.computation_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.input_receipts import amendment_input_receipt
from lexinform.models import (
    AmendmentInputReceipt,
    AmendmentRevisionTarget,
    AmendmentsContext,
    AmendmentsQuestion,
    AnalysisQuestion,
    BatchIntent,
    BatchItemMeta,
    BatchRequest,
    DeliveryPlan,
    DigestItemMeta,
    LlmBatch,
    LlmBatchItem,
)
from lexinform.models.batch import batch_item_meta
from lexinform.models.input_receipts import amendment_context_sha256
from lexinform.text_projection import SourcePiece, project_pieces
from tests.unit.test_input_receipt_validation import source
from tests.unit.test_input_receipts import NOW, SOURCE, selection


def request(text: str = SOURCE) -> BatchRequest:
    ctx = AmendmentsContext(
        number="3039",
        title="Poprawki 😀",
        source_kind="committee_amendments",
        text=text,
        truncated=False,
        previous_summary="Previous summary",
        previous_key_changes=["Prior"],
        proposal="przyjąć poprawki",
        recommendation="Separate recommendation",
    )
    return BatchRequest(
        custom_id="amendment-input",
        term=10,
        number=ctx.number,
        question=AmendmentsQuestion(ctx=ctx),
    ).freeze(
        payload_json=json.dumps({"amendments": ctx.model_dump(mode="json")}),
        model="gpt-6.1-sol",
        prompt_version="reader",
        estimated_cost_usd=0.01,
    )


def amendment_source(
    path: Path, *, parents: bool = False
) -> tuple[SqliteBillRepository, BatchIntent]:
    repo, original = source(path, parents=parents)
    assert isinstance(original.meta, BatchItemMeta), "the source fixture provides primary metadata"
    old = original.meta.prepared_input_receipt
    assert old is not None, "the source fixture provides a mapped primary receipt"
    assert isinstance(original.request.question, AnalysisQuestion), (
        "the fixture supplies primary text"
    )
    frozen = request(original.request.question.ctx.text)
    assert isinstance(frozen.question, AmendmentsQuestion), "the new request is amendments"
    raw = old.model_dump(mode="json")
    raw.update(
        profile="amendment-single-part-text-v1",
        context_encoding="amendments-context-json-v1",
        context_sha256=amendment_context_sha256(frozen.question.ctx),
        request_sha256=frozen.payload_sha256,
    )
    receipt = AmendmentInputReceipt.model_validate(raw)
    intent = BatchIntent.from_request(
        request=frozen,
        meta=DigestItemMeta(memo_key="amendment-key", prepared_input_receipt=receipt),
        provider="openai",
        created_at=NOW,
    )
    repo.delete_batch_intents([original.request.custom_id])
    repo.save_batch_intent(intent)
    return repo, intent


@pytest.mark.parametrize("correction", [False, True])
def test_reader_roundtrip_and_shared_builder_map_only_normative_text(correction: bool) -> None:
    frozen = request()
    receipt = amendment_input_receipt(
        frozen, selection(), projection=project_pieces(SOURCE, (SourcePiece(SOURCE, 0),))
    )
    assert isinstance(frozen.question, AmendmentsQuestion), "the fixture prepares amendments"
    assert (
        receipt.context_sha256
        == hashlib.sha256(
            json.dumps(
                frozen.question.ctx.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )
    assert receipt.text_chars == receipt.source_part.chars == len(SOURCE)
    assert receipt.source_part.text_sha256 == hashlib.sha256(SOURCE.encode()).hexdigest()
    assert len(receipt.segments) == 1 and receipt.segments[0].source_end == len(SOURCE)
    assert receipt.physical_origins == receipt.block_origins == "unmapped"
    assert receipt.exclusions == "unknown" and receipt.input_completeness == "not_asserted"
    assert all(
        text not in receipt.model_dump_json() for text in (SOURCE, "Previous", "recommendation")
    )
    intent = BatchIntent.from_request(
        request=frozen,
        meta=DigestItemMeta(
            memo_key="key",
            prepared_input_receipt=receipt,
            amendment_revision=AmendmentRevisionTarget(
                publication_id=1,
                channel_id="tech",
                source_url="https://source.test/amendments",
                delivery=DeliveryPlan(bill_json="{}", change_json="{}"),
            )
            if correction
            else None,
        ),
        provider="openai",
        created_at=NOW,
    )
    assert BatchIntent.model_validate_json(intent.model_dump_json()) == intent


@pytest.mark.parametrize(
    "fault",
    [
        "previous_summary",
        "previous_key_changes",
        "proposal",
        "recommendation",
        "text",
        "payload",
        "request_sha",
        "kind",
        "profile",
        "encoding",
        "range",
        "scan_meta",
    ],
)
def test_receipt_rejects_context_request_range_and_purpose_mismatch(
    tmp_path: Path, fault: str
) -> None:
    with closing(amendment_source(tmp_path / "source.db")[0]) as repo:
        raw: dict[str, Any] = repo.list_queued_batch_intents()[0].model_dump(mode="json")
    receipt = raw["meta"]["prepared_input_receipt"]
    if fault in ("previous_summary", "proposal", "recommendation", "text"):
        raw["request"]["question"]["ctx"][fault] += " changed"
    elif fault == "previous_key_changes":
        raw["request"]["question"]["ctx"][fault].append("changed")
    elif fault == "payload":
        raw["request"]["payload_json"] += " "
    elif fault == "request_sha":
        raw["meta"]["request_sha256"] = "e" * 64
    elif fault == "kind":
        raw["request"]["question"]["kind"] = "supplement"
        raw["request"]["question"]["ctx"]["document_title"] = "Title"
    elif fault == "profile":
        receipt["profile"] = "supplement-single-part-text-v1"
        receipt["context_encoding"] = "supplement-context-json-v1"
    elif fault == "encoding":
        receipt["context_encoding"] = "bill-context-json-v1"
    elif fault == "range":
        receipt["segments"][0]["source_end"] -= 1
    else:
        raw["meta"]["scan_input"] = {}
    with pytest.raises(ValidationError):
        BatchIntent.model_validate(raw)


@pytest.mark.parametrize("parents", [False, True])
def test_restore_doctor_and_submitted_metadata_share_reader(tmp_path: Path, parents: bool) -> None:
    path = tmp_path / "source.db"
    with closing(amendment_source(path, parents=parents)[0]) as repo:
        intent = repo.list_queued_batch_intents()[0]
        encoded = repo.dump()
        repo.restore(encoded)
        assert repo.dump() == encoded and repo.list_queued_batch_intents() == [intent]
        batch = LlmBatch(
            batch_id="amendments",
            provider="openai",
            call_kind="amendments",
            submitted_at=NOW,
            status="submitted",
            request_count=1,
            estimated_cost_usd=0,
        )
        item = LlmBatchItem.from_intent(batch.batch_id, intent)
        repo.save_llm_batch(batch, [item])
        repo.delete_batch_intents([intent.request.custom_id])
        script = repo.dump()
        repo.restore(script)
        assert repo.dump() == script
        parsed = batch_item_meta("amendments", item.meta.model_dump_json())
        assert parsed.prepared_input_receipt == intent.meta.prepared_input_receipt
        with pytest.raises(ValueError):
            batch_item_meta("supplement", item.meta.model_dump_json())
    assert audit(path)["status"] == "recorded"


@pytest.mark.parametrize(
    "field", ["context_sha256", "source_selection_id", "artifact_sha256", "extraction_id"]
)
def test_tampered_dependency_rejects_restore_atomically_and_reports_bounded_finding(
    tmp_path: Path, field: str
) -> None:
    path = tmp_path / "source.db"
    with closing(amendment_source(path, parents=True)[0]) as repo:
        valid = repo.dump()
        intent = repo.list_queued_batch_intents()[0]
        receipt = intent.meta.prepared_input_receipt
        assert receipt is not None, "the fixture has a mapped receipt"
        value = getattr(receipt, field)
        assert isinstance(value, str), "the controlled parent/checksum is known"
        bad = receipt.model_copy(
            update={field: (value.split(":")[0] + ":" if ":" in value else "") + "e" * 64}
        )
        repo.delete_batch_intents([intent.request.custom_id])
        repo.save_batch_intent(
            intent.model_copy(
                update={"meta": intent.meta.model_copy(update={"prepared_input_receipt": bad})}
            )
        )
        corrupt = repo.dump()
    report = audit(path)
    assert report["status"] == "failed"
    assert any(row["code"] == "invalid_input_intent_snapshot" for row in report["findings"])
    assert SOURCE not in json.dumps(report)
    with closing(SqliteBillRepository(path)) as repo:
        repo.restore(valid)
        with pytest.raises(ValueError, match="invalid_input_intent_snapshot"):
            repo.restore(corrupt)
        assert repo.dump() == valid
    assert SOURCE not in json.dumps(audit(path))


def test_resumed_context_without_receipt_stays_unknown_and_old_metadata_bytes_match() -> None:
    metadata = DigestItemMeta(memo_key="legacy")
    intent = BatchIntent.from_request(
        request=request(), meta=metadata, provider="openai", created_at=NOW
    )
    assert intent.meta.prepared_input_receipt is None
    assert "prepared_input_receipt" not in intent.model_dump_json()
    assert DigestItemMeta.model_validate_json(metadata.model_dump_json()) == metadata
    receipt = amendment_input_receipt(request(), selection(), projection=None)
    assert receipt.retention == "unknown" and receipt.segment_count is None and not receipt.segments


@pytest.mark.parametrize("fault", ["purpose", "checksum", "range"])
def test_submitted_amendment_reader_rejects_laundered_metadata(tmp_path: Path, fault: str) -> None:
    with closing(amendment_source(tmp_path / "source.db")[0]) as repo:
        original = repo.list_queued_batch_intents()[0]
    item = LlmBatchItem.from_intent("amendments", original)
    raw = item.meta.model_dump(mode="json")
    if fault == "checksum":
        raw["request_sha256"] = None
    elif fault == "range":
        raw["prepared_input_receipt"]["segments"][0]["source_end"] -= 1
    with pytest.raises(ValueError):
        batch_item_meta("joint" if fault == "purpose" else "amendments", json.dumps(raw))

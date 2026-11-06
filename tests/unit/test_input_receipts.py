import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.input_receipts import primary_input_receipt
from lexinform.models import (
    AnalysisQuestion,
    ApplicantType,
    ArtifactIdentity,
    BatchIntent,
    BatchItemMeta,
    BatchRequest,
    BillContext,
    BillStatus,
    DocumentPart,
    DocumentSelection,
    DocumentSelectionRecord,
    LlmBatch,
    LlmBatchItem,
    PrimaryInputReceipt,
    ProcessSummary,
)
from lexinform.models.batch import FrozenRequestManifest
from lexinform.models.input_receipts import MAX_INPUT_RECEIPT_BYTES, primary_context_sha256
from lexinform.prepared_input import prepared_text_input
from lexinform.text_projection import SourcePiece, TextProjection, TextSegment, project_pieces
from tests.unit.test_physical_extraction import docx, extractor

NOW = datetime(2026, 10, 30, tzinfo=UTC)
SOURCE = "USTAWA — Art. 1. Cudzoziemiec otrzymuje 300 zł."


def request(text: str = SOURCE) -> BatchRequest:
    ctx = BillContext(
        number="3039",
        title="Projekt ustawy",
        description=None,
        document_date=NOW.date(),
        applicant_type=ApplicantType.UNKNOWN,
        text=text,
        truncated=False,
        text_source="pdf",
        forgotten_at=NOW,
        previous_key_changes=["Без изменения", "😀"],
    )
    draft = BatchRequest(
        custom_id="input",
        term=10,
        number="3039",
        question=AnalysisQuestion(kind="analysis", ctx=ctx),
    )
    return draft.freeze(
        payload_json=json.dumps({"body": ctx.model_dump(mode="json")}),
        model="gpt-6.1-sol",
        prompt_version="pinned",
        estimated_cost_usd=0.01,
    )


def selection(text: str = SOURCE) -> DocumentSelection:
    return DocumentSelection(
        parts=(
            DocumentPart(
                role="bill",
                text=text,
                url="https://example.test/ustawa/ą",
                text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            ),
        )
    )


def meta(receipt: PrimaryInputReceipt | None = None) -> BatchItemMeta:
    return BatchItemMeta(
        input_chars=len(SOURCE),
        truncated=False,
        text_source="pdf",
        source_kind="print",
        source_url=None,
        revision=1,
        text_sha256=None,
        memo_key="frozen-key",
        prepared_input_receipt=receipt,
    )


def test_intent_text_reference_restores_frozen_input_and_rejects_a_wrong_hash(
    tmp_path: Path,
) -> None:
    path = tmp_path / "intent.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        text = SOURCE + "\nZażółć gęślą."
        frozen = request(text)
        metadata = meta().model_copy(update={"text": text})
        intent = BatchIntent.from_request(
            request=frozen, meta=metadata, provider="openai", created_at=NOW
        )
        repo.save_batch_intent(intent)
        inline = repo.dump()
        with closing(sqlite3.connect(path)) as sql, sql:
            marker = {
                "profile": "question-text-v1",
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
            sql.execute(
                "UPDATE llm_batch_intents SET meta_json=json_set(meta_json,'$.text',json(?))",
                (json.dumps(marker),),
            )
        compact = repo.dump()
        for _ in range(2):
            repo.restore(compact)
            assert repo.dump() == compact
        (queued,) = repo.list_queued_batch_intents()
        assert queued == intent
        repo.mark_batch_intents_submitting([intent.request.custom_id])
        (submitting,) = repo.list_submitting_batch_intents()
        assert LlmBatchItem.from_intent("batch", submitting) == LlmBatchItem.from_intent(
            "batch", intent
        )
        assert submitting.request.model_dump_json() == frozen.model_dump_json()
        with closing(sqlite3.connect(path)) as sql, sql:
            sql.execute(
                "UPDATE llm_batch_intents SET meta_json=json_set(meta_json,'$.text.sha256',?)",
                ("0" * 64,),
            )
        invalid = repo.dump()
        with pytest.raises(ValueError, match="invalid_intent_question_text_reference"):
            repo.list_submitting_batch_intents()
        repo.restore(inline)
        with pytest.raises(ValueError, match="invalid_input_intent_snapshot"):
            repo.restore(invalid)
        assert repo.dump() == inline and repo.list_queued_batch_intents() == [intent]


def test_submitted_request_manifest_survives_cleanup_and_rejects_foreign_provider(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manifest.db"
    frozen = request()
    assert frozen.payload_sha256 is not None and frozen.payload_json is not None, (
        "the fixture freezes its request"
    )
    manifest = FrozenRequestManifest(
        provider="openai",
        purpose="analysis",
        request_sha256=frozen.payload_sha256,
        model=frozen.model,
        prompt_version=frozen.prompt_version,
        payload_bytes=len(frozen.payload_json.encode()),
        messages_sha256="a" * 64,
        output_format_sha256="b" * 64,
        options_sha256="c" * 64,
    )
    intent = BatchIntent.from_request(
        request=frozen, meta=meta(), provider="openai", created_at=NOW
    )
    batch = LlmBatch(
        batch_id="manifest",
        provider="openai",
        call_kind="analysis",
        submitted_at=NOW,
        status="submitted",
        request_count=1,
        estimated_cost_usd=0.01,
    )
    item = LlmBatchItem.from_intent(batch.batch_id, intent)
    item = item.model_copy(
        update={
            "meta": item.meta.model_copy(
                update={
                    "text": SOURCE,
                    "frozen_request_manifest": manifest,
                }
            )
        }
    )
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        before = repo.dump()
        with pytest.raises(ValueError, match="batch provider"):
            repo.save_llm_batch(batch.model_copy(update={"provider": "anthropic"}), [item])
        assert repo.dump() == before
        repo.save_llm_batch(batch, [item])
        stored = repo.dump()
        repo.restore(stored)
        assert repo.dump() == stored and repo.list_llm_batch_items(batch.batch_id) == [item]
        with closing(sqlite3.connect(path)) as sql, sql:
            sql.execute(
                "UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.frozen_request_manifest.provider','anthropic')"
            )
        invalid = repo.dump()
        repo.restore(stored)
        with pytest.raises(ValueError, match="invalid_input_item_snapshot"):
            repo.restore(invalid)
        assert repo.dump() == stored
        repo.upsert_summary(
            ProcessSummary(
                term=10, number="3039", title="Project", document_type="projekt", change_date=NOW
            ),
            now=NOW,
        )
        repo.set_status(10, "3039", BillStatus.ANALYZED)
        repo.mark_llm_batch_item_consumed(batch.batch_id, item.custom_id, consumed_at=NOW)
        repo.mark_batch_item_accounted(batch.batch_id, item.custom_id, at=NOW)
        repo.mark_llm_batch_forgotten(batch.batch_id, forgotten_at=NOW)
        future = NOW + timedelta(days=181)
        assert repo.prune_history(before=future, memo_before=future)["batch_payloads"] == 1
        retained = repo.dump()
        assert '"frozen_request_manifest"' in retained and SOURCE not in retained
        repo.restore(retained)
        assert repo.dump() == retained


@pytest.mark.parametrize("intent_state", ["submitting", "queued", "missing", "wrong_owner"])
def test_manifest_capture_requires_matching_submitting_intent(intent_state: str) -> None:
    draft = request().model_copy(update={"payload_json": None, "payload_sha256": None})
    payload = json.dumps(
        {
            "custom_id": draft.custom_id,
            "method": "POST",
            "url": "/v1/responses",
            "body": {
                "model": "gpt-6.1-sol",
                "input": [],
                "text": {"format": {"type": "json_schema"}},
            },
        }
    )
    frozen = draft.freeze(
        payload_json=payload, model="gpt-6.1-sol", prompt_version="pinned", estimated_cost_usd=0.01
    )
    intent = BatchIntent.from_request(
        request=frozen, meta=meta(), provider="openai", created_at=NOW
    )
    batch = LlmBatch(
        batch_id="manifest",
        provider="openai",
        call_kind="analysis",
        submitted_at=NOW,
        status="submitted",
        request_count=1,
        estimated_cost_usd=0.01,
    )
    item = LlmBatchItem.from_intent(batch.batch_id, intent)
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        if intent_state != "missing":
            repo.save_batch_intent(intent)
        if intent_state in ("submitting", "wrong_owner"):
            repo.mark_batch_intents_submitting([item.custom_id])
        if intent_state == "wrong_owner":
            item = item.model_copy(update={"number": "3040"})
        repo.save_llm_batch(batch, [item])
        (stored,) = repo.list_llm_batch_items(batch.batch_id)
        manifest = stored.meta.frozen_request_manifest
        assert (manifest is not None) == (intent_state == "submitting")
        if manifest is not None:
            assert manifest.request_sha256 == frozen.payload_sha256
            assert manifest.payload_bytes == len(payload.encode())


def test_provider_payload_reference_reconstructs_the_exact_frozen_bytes(tmp_path: Path) -> None:
    path = tmp_path / "provider-intent.db"
    text = 'Tekst „ą” with "quotes", a backslash \\ and\nnew line.'
    draft = request(text).model_copy(update={"payload_json": None, "payload_sha256": None})
    payload = json.dumps({"content": f"fixed prompt\n{text}\nfixed suffix"}, ensure_ascii=False)
    frozen = draft.freeze(
        payload_json=payload, model="gpt-6.1-sol", prompt_version="frozen", estimated_cost_usd=0.01
    )
    intent = BatchIntent.from_request(
        request=frozen, meta=meta(), provider="openai", created_at=NOW
    )
    escaped = json.dumps(text, ensure_ascii=False)[1:-1]
    prefix, _, suffix = payload.partition(escaped)
    reference = {"profile": "question-text-json-v1", "prefix": prefix, "suffix": suffix}
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_batch_intent(intent)
        inline = repo.dump()
        with closing(sqlite3.connect(path)) as sql, sql:
            sql.execute(
                "UPDATE llm_batch_intents SET request_json=json_set(request_json,'$.payload_json',json(?))",
                (json.dumps(reference),),
            )
        compact = repo.dump()
        repo.restore(compact)
        assert repo.dump() == compact and repo.list_queued_batch_intents() == [intent]
        repo.mark_batch_intents_submitting([frozen.custom_id])
        (submitting,) = repo.list_submitting_batch_intents()
        assert submitting.request.model_dump_json() == frozen.model_dump_json()
        with closing(sqlite3.connect(path)) as sql, sql:
            sql.execute(
                "UPDATE llm_batch_intents SET request_json=json_set(request_json,'$.payload_json.prefix','tampered')"
            )
        invalid = repo.dump()
        repo.restore(inline)
        with pytest.raises(ValueError, match="invalid_input_intent_snapshot"):
            repo.restore(invalid)
        assert repo.dump() == inline


@pytest.mark.parametrize("profile", ["unique", "repeated", "overlapping", "short", "missing"])
def test_only_unique_large_provider_payload_text_is_shared(tmp_path: Path, profile: str) -> None:
    path = tmp_path / "provider-intent.db"
    text = "abc" if profile == "short" else "x" * 32768
    content = {
        "unique": text,
        "repeated": text + "\n" + text,
        "overlapping": text + "x",
        "short": text,
        "missing": "other text",
    }[profile]
    payload = json.dumps({"content": content}, ensure_ascii=False)
    frozen = (
        request(text)
        .model_copy(update={"payload_json": None, "payload_sha256": None})
        .freeze(
            payload_json=payload,
            model="gpt-6.1-sol",
            prompt_version="pinned",
            estimated_cost_usd=0.01,
        )
    )
    intent = BatchIntent.from_request(
        request=frozen, meta=meta(), provider="openai", created_at=NOW
    )
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_batch_intent(intent)
        with closing(sqlite3.connect(path)) as sql:
            (raw,) = sql.execute("SELECT request_json FROM llm_batch_intents").fetchone()
        if profile == "unique":
            assert json.loads(raw)["payload_json"]["profile"] == "question-text-json-v1"
        else:
            assert raw == frozen.model_dump_json()
        stored = repo.dump()
        repo.restore(stored)
        assert repo.dump() == stored and repo.list_queued_batch_intents() == [intent]


@pytest.mark.parametrize("profile", ["exact", "different", "short", "unfrozen"])
def test_only_large_exact_frozen_question_text_is_shared(tmp_path: Path, profile: str) -> None:
    path = tmp_path / "intent.db"
    text = SOURCE if profile == "short" else SOURCE * 1000
    frozen = request(text)
    if profile == "unfrozen":
        frozen = frozen.model_copy(update={"payload_json": None, "payload_sha256": None})
    metadata = meta().model_copy(update={"text": text + "!" if profile == "different" else text})
    intent = BatchIntent.from_request(
        request=frozen, meta=metadata, provider="openai", created_at=NOW
    )
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_batch_intent(intent)
        repo.save_batch_intent(intent)
        with closing(sqlite3.connect(path)) as sql:
            raw_request, raw_meta = sql.execute(
                "SELECT request_json, meta_json FROM llm_batch_intents"
            ).fetchone()
        assert raw_request == frozen.model_dump_json()
        if profile == "exact":
            assert json.loads(raw_meta)["text"] == {
                "profile": "question-text-v1",
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        else:
            assert raw_meta == intent.meta.model_dump_json()
        assert repo.list_queued_batch_intents() == [intent]


def test_receipt_matches_existing_j125_order_roles_and_actual_frozen_bytes() -> None:
    source = selection()
    projection = project_pieces(
        SOURCE, (SourcePiece(SOURCE[:10], 0), SourcePiece(" OMIT "), SourcePiece(SOURCE[20:], 20))
    )
    frozen = request(projection.text)
    before = frozen.model_dump_json()
    known = DocumentSelectionRecord.from_selection(source)
    receipt = primary_input_receipt(frozen, source, projection=projection, known_selection=known)
    old = prepared_text_input(source, projection.text, projection=projection)
    assert receipt.segments == old.segments and receipt.source_part == old.parts[0]
    assert receipt.source_selection_id == known.selection_id
    assert receipt.request_sha256 == frozen.payload_sha256
    assert (
        receipt.context_sha256
        == hashlib.sha256(
            json.dumps(
                frozen.ctx.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )
    assert frozen.model_dump_json() == before
    assert receipt.retention == "complete" and receipt.input_completeness == "not_asserted"
    assert receipt.block_origins == receipt.physical_origins == "unmapped"
    assert receipt.exclusions == "unknown"
    encoded = receipt.model_dump_json()
    assert SOURCE not in encoded and "example.test" not in encoded and "OMIT" not in encoded
    assert PrimaryInputReceipt.model_validate_json(encoded) == receipt


def test_equal_text_without_projection_remains_unknown_and_old_metadata_omits_receipt() -> None:
    receipt = primary_input_receipt(request(), selection(), projection=None)
    assert receipt.retention == "unknown" and receipt.reason == "projection_unavailable"
    assert (
        receipt.segment_count is None and receipt.full_map_sha256 is None and not receipt.segments
    )
    assert (
        receipt.source_selection_id is None
        and receipt.artifact_sha256 is None
        and receipt.extraction_id is None
    )
    before = meta().model_dump_json()
    assert "prepared_input_receipt" not in before
    assert BatchItemMeta.model_validate_json(before).model_dump_json() == before


def test_legacy_hashless_selection_is_not_invented_as_a_known_text_reference() -> None:
    source = selection()
    source = source.model_copy(
        update={"parts": (source.parts[0].model_copy(update={"text_sha256": ""}),)}
    )
    known = DocumentSelectionRecord.from_selection(source)
    receipt = primary_input_receipt(request(), source, projection=None, known_selection=known)
    assert receipt.source_selection_id is None and receipt.source_part.text_sha256


def test_streamed_selection_identity_matches_existing_nested_unicode_metadata() -> None:
    source = selection().model_copy(
        update={
            "observed_parts": (
                DocumentPart(
                    text="not retained",
                    url="https://example.test/" + "ą😀" * 2000,
                    losses=("unknown",),
                ),
            )
        }
    )
    known = DocumentSelectionRecord.from_selection(source)
    receipt = primary_input_receipt(request(), source, projection=None, known_selection=known)
    assert receipt.source_selection_id == known.selection_id


@pytest.mark.parametrize(
    "fault", ["payload", "context", "source_hash", "copy", "source", "selection", "body", "package"]
)
def test_receipt_refuses_uncorroborated_requests_sources_and_ranges(fault: str) -> None:
    source = selection("ABCD")
    projection = project_pieces("ABCD", (SourcePiece("AB", 0),))
    frozen = request(projection.text)
    known = None
    body = None
    if fault == "payload":
        frozen = frozen.model_copy(update={"payload_json": "changed"})
    elif fault == "context":
        frozen = frozen.model_copy(
            update={
                "question": AnalysisQuestion(
                    kind="analysis", ctx=frozen.question.ctx.model_copy(update={"text": "changed"})
                )
            }
        )
    elif fault == "source_hash":
        source = source.model_copy(
            update={"parts": (source.parts[0].model_copy(update={"text_sha256": "a" * 64}),)}
        )
    elif fault == "copy":
        projection = TextProjection(
            "AB", hashlib.sha256(b"ABCD").hexdigest(), 4, (TextSegment(0, 2, 1),)
        )
    elif fault == "source":
        projection = project_pieces("foreign", (SourcePiece("AB"),))
    elif fault == "selection":
        known = DocumentSelectionRecord.from_selection(selection("different"))
    elif fault == "body":
        body = ArtifactIdentity(sha256="b" * 64, byte_size=4)
    else:
        source = source.model_copy(
            update={"parts": (source.parts[0].model_copy(update={"members": ("member.docx",)}),)}
        )
    with pytest.raises(ValueError):
        primary_input_receipt(
            frozen, source, projection=projection, known_selection=known, body=body
        )


def test_known_native_extraction_and_body_are_corroborated_independently() -> None:
    data = docx(SOURCE)
    observed = extractor().extract_document_with_provenance(data)
    result = observed.result
    text = observed.document.text
    body = ArtifactIdentity(sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data))
    source = DocumentSelection(parts=(DocumentPart(role="bill", text=text, sha256=body.sha256),))
    projection = project_pieces(text, (SourcePiece(text, 0),))
    receipt = primary_input_receipt(
        request(text), source, projection=projection, body=body, extraction=result
    )
    assert receipt.artifact_sha256 == body.sha256 and receipt.extraction_id == result.result_id
    assert receipt.source_selection_id is None
    bad = result.model_copy(
        update={
            "selected_parts": (
                result.selected_parts[0].model_copy(update={"text_sha256": "f" * 64}),
            )
        }
    )
    with pytest.raises(ValueError, match="extraction"):
        primary_input_receipt(
            request(text), source, projection=projection, body=body, extraction=bad
        )


def test_large_map_retains_only_32_ordered_segments_and_hashes_the_entire_observed_map() -> None:
    text = "0123456789" * 200
    source = selection(text)
    pieces = tuple(
        piece
        for index, char in enumerate(text)
        for piece in (SourcePiece(char, index), SourcePiece("!"))
    )
    projection = project_pieces(text, pieces)
    receipt = primary_input_receipt(request(projection.text), source, projection=projection)
    old = prepared_text_input(source, projection.text, projection=projection)
    actual_full = json.dumps(
        [segment.model_dump(mode="json") for segment in old.segments],
        sort_keys=True,
        separators=(",", ":"),
    )
    assert receipt.segment_count == 4000 and len(receipt.segments) == 32
    assert receipt.segments == old.segments[:32]
    assert receipt.full_map_sha256 == hashlib.sha256(actual_full.encode()).hexdigest()
    assert receipt.retention == "prefix" and receipt.reason == "segment_limit"
    assert len(receipt.model_dump_json().encode()) <= MAX_INPUT_RECEIPT_BYTES
    assert receipt.segments[-1].end < receipt.text_chars


@pytest.mark.parametrize(
    "fault",
    ["count", "boolean", "hash", "scope", "whole", "part", "order", "oversized", "body_text"],
)
def test_reader_rejects_false_completeness_corrupt_maps_and_oversized_fields(fault: str) -> None:
    receipt = primary_input_receipt(
        request(), selection(), projection=project_pieces(SOURCE, (SourcePiece(SOURCE, 0),))
    )
    value: dict[str, Any] = receipt.model_dump(mode="json")
    if fault == "count":
        value["segment_count"] = 2**63
    elif fault == "boolean":
        value["segment_count"] = True
    elif fault == "hash":
        value["full_map_sha256"] = "a" * 64
    elif fault == "scope":
        value["physical_origins"] = "known"
    elif fault == "whole":
        value["input_completeness"] = "complete"
    elif fault == "part":
        value["segments"][0]["part_index"] = 1
    elif fault == "order":
        value["segments"][0]["start"] = 1
    elif fault == "oversized":
        value["segments"] = value["segments"] * 33
    else:
        value["text"] = SOURCE
    with pytest.raises(ValidationError):
        PrimaryInputReceipt.model_validate(value)


@pytest.mark.parametrize("fault", ["context", "request", "text", "meta"])
def test_intent_reader_checks_receipt_against_its_actual_request(fault: str) -> None:
    frozen = request()
    receipt = primary_input_receipt(frozen, selection(), projection=None)
    intent = BatchIntent(request=frozen, meta=meta(receipt), provider="openai", created_at=NOW)
    value: dict[str, Any] = intent.model_dump(mode="json")
    if fault == "context":
        value["request"]["question"]["ctx"]["title"] = "Different title"
    elif fault == "request":
        value["meta"]["prepared_input_receipt"]["request_sha256"] = "a" * 64
    elif fault == "text":
        value["meta"]["prepared_input_receipt"]["text_sha256"] = "b" * 64
    else:
        value["meta"]["request_sha256"] = "c" * 64
    with pytest.raises(ValidationError):
        BatchIntent.model_validate(value)
    assert BatchIntent.model_validate_json(intent.model_dump_json()) == intent
    assert isinstance(frozen.question, AnalysisQuestion), "request creates a primary question"
    assert primary_context_sha256(frozen.question.ctx) == receipt.context_sha256


def test_typed_corrupt_meta_cannot_fall_back_to_secondary_metadata() -> None:
    frozen = request()
    receipt = primary_input_receipt(frozen, selection(), projection=None)
    corrupt = meta(receipt).model_copy(update={"request_sha256": "e" * 64})
    with pytest.raises(ValidationError):
        BatchIntent(request=frozen, meta=corrupt, provider="openai", created_at=NOW)


def test_receipt_survives_existing_intent_dump_restore_without_rekeying(
    repo: SqliteBillRepository,
) -> None:
    frozen = request()
    receipt = primary_input_receipt(frozen, selection(), projection=None)
    intent = BatchIntent.from_request(
        request=frozen, meta=meta(receipt), provider="openai", created_at=NOW
    )
    before = frozen.model_dump_json()
    repo.save_batch_intent(intent)
    sql = repo.dump()
    repo.restore(sql)
    assert repo.list_queued_batch_intents() == [intent]
    loaded = repo.list_queued_batch_intents()[0]
    assert loaded.request.model_dump_json() == before
    assert loaded.meta.input_binding == intent.meta.input_binding
    repo.save_batch_intent(intent)
    assert repo.dump() == sql

from dataclasses import dataclass, replace

import pytest

from lexinform.input_receipts import primary_input_receipt
from lexinform.models import (
    AnalysisQuestion,
    ArtifactIdentity,
    BatchIntent,
    BatchItemMeta,
    DocumentPart,
    DocumentSelection,
    PreparedTextSegment,
)
from lexinform.models.analysis import AnalysisRecord
from lexinform.models.input_receipts import PrimaryInputReceipt, input_map_sha256
from lexinform.models.reviews import ReviewDocument
from lexinform.primary_claim_input import ClaimInputCheck, validate_key_change_input
from lexinform.primary_claims import validate_key_change_evidence
from lexinform.text_projection import SourcePiece, project_pieces
from tests.unit.test_input_receipts import NOW, meta, request
from tests.unit.test_primary_claims import QUOTE, evidence, record, source


@dataclass(frozen=True)
class Case:
    record: AnalysisRecord
    document: ReviewDocument
    intent: BatchIntent


def case(*, pieces: tuple[SourcePiece, ...] | None = None, selected_start: int = 0) -> Case:
    document = source("Header\n" + QUOTE + "\nFooter")
    projection = project_pieces(document.text, pieces or (SourcePiece(document.text, 0),))
    frozen = request(projection.text)
    selected = DocumentSelection(
        parts=(
            DocumentPart(
                url=document.url,
                role="bill",
                sha256=document.file_sha256 or "",
                text=document.text,
                text_sha256=document.sha256,
                text_start=selected_start,
                text_end=selected_start + len(document.text),
            ),
        )
    )
    receipt = primary_input_receipt(
        frozen,
        selected,
        projection=projection,
        body=ArtifactIdentity(sha256=document.file_sha256 or "", byte_size=1),
    )
    metadata = meta().model_copy(
        update={
            "input_chars": len(projection.text),
            "source_url": document.url,
            "selection": selected.without_text(),
            "prepared_input_receipt": receipt,
        }
    )
    intent = BatchIntent.from_request(
        request=frozen, meta=metadata, provider="openai", created_at=NOW
    )
    answer = record().model_copy(
        update={
            "request_sha256": frozen.payload_sha256,
            "input_chars": len(projection.text),
            "source_url": document.url,
        }
    )
    return Case(answer, document, intent)


def checked(item: Case) -> ClaimInputCheck:
    return validate_key_change_input(
        item.record, evidence(item.document), (item.document,), intent=item.intent
    )


def change_meta(item: Case, **updates: object) -> Case:
    return replace(
        item,
        intent=item.intent.model_copy(update={"meta": item.intent.meta.model_copy(update=updates)}),
    )


def test_retained_span_uses_actual_context_offsets_and_keeps_every_input_unchanged() -> None:
    item = case(pieces=(SourcePiece("PROMPT\n"), SourcePiece(QUOTE, 7)))
    snapshots = (
        item.record.model_dump_json(),
        item.document.model_dump_json(),
        item.intent.model_dump_json(),
    )
    result = checked(item)
    assert result.status == "retained" and result.input_span == (7, 7 + len(QUOTE))
    assert result.evidence == validate_key_change_evidence(
        item.record, evidence(item.document), (item.document,)
    )
    assert result.semantic == "semantic_not_assessed"
    assert result.block_origins == result.physical_origins == "unmapped"
    assert snapshots == (
        item.record.model_dump_json(),
        item.document.model_dump_json(),
        item.intent.model_dump_json(),
    )


def test_source_fragment_coordinates_are_local_to_the_selected_text() -> None:
    item = case(selected_start=400)
    assert checked(item).input_span == (7, 7 + len(QUOTE))


def test_generated_replacement_of_one_space_does_not_prove_continuous_source_retention() -> None:
    split = QUOTE.index(" ")
    item = case(
        pieces=(
            SourcePiece(QUOTE[:split], 7),
            SourcePiece(" "),
            SourcePiece(QUOTE[split + 1 :], 8 + split),
        )
    )
    assert isinstance(item.intent.request.question, AnalysisQuestion), "the fixture is primary text"
    assert item.intent.request.question.ctx.text == QUOTE
    result = checked(item)
    assert result.status == "omitted" and result.reason == "no_continuous_source_mapping"
    assert result.input_span is None and result.evidence.status == "quote_present"


def test_generated_duplicate_cannot_substitute_for_the_selected_source_span() -> None:
    item = case(pieces=(SourcePiece(QUOTE),))
    assert checked(item).status == "omitted"


def test_a_complete_map_can_certify_omission_without_claiming_all_quote_characters_absent() -> None:
    item = case(pieces=(SourcePiece(QUOTE[:25], 7),))
    result = checked(item)
    assert result.status == "omitted" and result.input_span is None
    assert result.evidence.status == "quote_present"


@pytest.mark.parametrize("generated", [False, True])
def test_multiple_literal_input_positions_remain_unknown(generated: bool) -> None:
    item = case(
        pieces=(
            SourcePiece(QUOTE, 7),
            SourcePiece("\n"),
            SourcePiece(QUOTE, None if generated else 7),
        )
    )
    result = checked(item)
    assert result.status == "unknown" and result.reason == "input_positions_ambiguous"


def test_adjacent_segments_must_be_contiguous_in_source_and_output() -> None:
    item = case(pieces=(SourcePiece(QUOTE, 7),))
    receipt = item.intent.meta.prepared_input_receipt
    assert receipt is not None, "the fixture carries the actual frozen map"
    segments = (
        PreparedTextSegment(
            start=0, end=10, origin="source", part_index=0, source_start=7, source_end=17
        ),
        PreparedTextSegment(
            start=10,
            end=len(QUOTE),
            origin="source",
            part_index=0,
            source_start=17,
            source_end=7 + len(QUOTE),
        ),
    )
    updated = receipt.model_copy(
        update={
            "segments": segments,
            "segment_count": 2,
            "full_map_sha256": input_map_sha256(segments),
        }
    )
    assert checked(change_meta(item, prepared_input_receipt=updated)).status == "retained"


@pytest.mark.parametrize("mapping", ["prefix", "unknown"])
def test_partial_or_unavailable_maps_never_prove_uniqueness(mapping: str) -> None:
    item = case()
    receipt = item.intent.meta.prepared_input_receipt
    assert receipt is not None, "the fixture has a complete observed map"
    updated = receipt.model_copy(
        update={
            "retention": "prefix" if mapping == "prefix" else "unknown",
            "reason": "segment_limit" if mapping == "prefix" else "projection_unavailable",
            "segment_count": 2 if mapping == "prefix" else None,
            "full_map_sha256": receipt.full_map_sha256 if mapping == "prefix" else None,
            "segments": receipt.segments if mapping == "prefix" else (),
        }
    )
    assert checked(change_meta(item, prepared_input_receipt=updated)).status == "unknown"


@pytest.mark.parametrize(
    "fault",
    [
        "request_missing",
        "request_wrong",
        "receipt",
        "part_url",
        "part_file",
        "part_hash",
        "part_length",
        "part_role",
        "part_missing_length",
        "multipart",
        "record_chars",
        "meta_chars",
        "record_url",
        "meta_url",
    ],
)
def test_missing_or_mismatched_request_and_selected_source_bindings_stay_unknown(
    fault: str,
) -> None:
    item = case()
    assert isinstance(item.intent.meta, BatchItemMeta), "the fixture is a primary request"
    metadata = item.intent.meta
    part = metadata.selection.parts[0]
    if fault == "request_missing":
        item = replace(item, record=item.record.model_copy(update={"request_sha256": None}))
    elif fault == "request_wrong":
        item = replace(item, record=item.record.model_copy(update={"request_sha256": "a" * 64}))
    elif fault == "receipt":
        item = change_meta(item, prepared_input_receipt=None)
    elif fault == "record_chars":
        item = replace(item, record=item.record.model_copy(update={"input_chars": 1}))
    elif fault == "meta_chars":
        item = change_meta(item, input_chars=1)
    elif fault == "record_url":
        item = replace(
            item, record=item.record.model_copy(update={"source_url": "https://else.test"})
        )
    elif fault == "meta_url":
        item = change_meta(item, source_url="https://else.test")
    else:
        changes: dict[str, object] = {
            "url": "https://else.test" if fault == "part_url" else part.url,
            "sha256": "a" * 64 if fault == "part_file" else part.sha256,
            "text_sha256": "a" * 64 if fault == "part_hash" else part.text_sha256,
            "text_end": None
            if fault == "part_missing_length"
            else 1
            if fault == "part_length"
            else part.text_end,
            "role": "osr" if fault == "part_role" else part.role,
        }
        changed = part.model_copy(update=changes)
        parts = (changed, changed) if fault == "multipart" else (changed,)
        item = change_meta(item, selection=metadata.selection.model_copy(update={"parts": parts}))
    assert checked(item).status == "unknown"


@pytest.mark.parametrize("fault", ["payload", "context", "receipt_context", "receipt_text"])
def test_revalidating_the_intent_rejects_typed_model_copy_tampering(fault: str) -> None:
    item = case()
    if fault == "payload":
        changed = item.intent.request.model_copy(update={"payload_json": "changed"})
        item = replace(item, intent=item.intent.model_copy(update={"request": changed}))
    elif fault == "context":
        question = item.intent.request.question
        changed_question = question.model_copy(
            update={"ctx": question.ctx.model_copy(update={"text": "changed"})}
        )
        item = replace(
            item,
            intent=item.intent.model_copy(
                update={
                    "request": item.intent.request.model_copy(update={"question": changed_question})
                }
            ),
        )
    else:
        receipt = item.intent.meta.prepared_input_receipt
        assert receipt is not None, "the fixture retains frozen input hashes"
        field = "context_sha256" if fault == "receipt_context" else "text_sha256"
        item = change_meta(
            item, prepared_input_receipt=receipt.model_copy(update={field: "a" * 64})
        )
    assert checked(item).status == "unknown"


def test_independent_slice_check_rejects_a_rehashed_forged_source_map() -> None:
    item = case(pieces=(SourcePiece("Header", 0), SourcePiece(QUOTE, 7)))
    receipt = item.intent.meta.prepared_input_receipt
    assert receipt is not None, "the fixture retains two independently copied ranges"
    segments = (
        receipt.segments[0].model_copy(update={"source_start": 7, "source_end": 13}),
        *receipt.segments[1:],
    )
    forged = receipt.model_copy(
        update={"segments": segments, "full_map_sha256": input_map_sha256(segments)}
    )
    result = checked(change_meta(item, prepared_input_receipt=forged))
    assert result.status == "unknown" and result.reason == "source_map_mismatch"


def test_full_extracted_document_cannot_replace_the_selected_fragment() -> None:
    item = case()
    larger = source("Different introduction\n" + item.document.text)
    result = validate_key_change_input(item.record, evidence(larger), (larger,), intent=item.intent)
    assert result.evidence.status == "quote_present" and result.status == "unknown"


def test_missing_intent_keeps_the_original_evidence_result() -> None:
    item = case()
    result = validate_key_change_input(
        item.record, evidence(item.document), (item.document,), intent=None
    )
    assert result.status == "unknown" and result.reason == "intent_missing"
    assert result.evidence.status == "quote_present"
    missing = validate_key_change_input(item.record, None, (item.document,), intent=item.intent)
    assert missing.evidence.status == "legacy_missing" and missing.status == "unknown"


def test_unique_normalized_quote_has_no_literal_input_claim() -> None:
    item = case()
    document = source(QUOTE.replace(" ", "\n\t"))
    result = validate_key_change_input(
        item.record, evidence(document), (document,), intent=item.intent
    )
    assert result.evidence.status == "quote_present" and result.evidence.observed_text_span is None
    assert result.status == "unknown"


@pytest.mark.parametrize("fault", ["file_missing", "file_wrong", "url_missing", "url_wrong"])
def test_known_source_identity_cannot_be_removed_or_replaced(fault: str) -> None:
    item = case()
    update = (
        {"file_sha256": None if fault == "file_missing" else "a" * 64}
        if fault.startswith("file")
        else {"url": "" if fault == "url_missing" else "https://else.test/source.pdf"}
    )
    document = item.document.model_copy(update=update)
    result = validate_key_change_input(
        item.record, evidence(document), (document,), intent=item.intent
    )
    assert result.evidence.status == "quote_present" and result.status == "unknown"


@pytest.mark.parametrize("fault", ["outer_subject", "binding_owner"])
def test_outer_request_and_known_input_owner_must_match_the_frozen_subject(fault: str) -> None:
    item = case()
    if fault == "outer_subject":
        item = replace(
            item,
            intent=item.intent.model_copy(
                update={"request": item.intent.request.model_copy(update={"number": "other"})}
            ),
        )
    else:
        binding = item.intent.meta.input_binding
        assert binding is not None, "from_request binds the input owner"
        item = change_meta(item, input_binding=binding.model_copy(update={"number": "other"}))
    result = checked(item)
    assert result.status == "unknown" and result.reason == "request_binding_mismatch"


def test_relabelled_supporting_text_cannot_become_selected_project_evidence() -> None:
    item = case()
    assert isinstance(item.intent.meta, BatchItemMeta), (
        "the controlled request has primary metadata"
    )
    receipt = item.intent.meta.prepared_input_receipt
    assert isinstance(receipt, PrimaryInputReceipt), (
        "the controlled input has one frozen source part"
    )
    part = item.intent.meta.selection.parts[0].model_copy(update={"role": "justification"})
    receipt = receipt.model_copy(
        update={"source_part": receipt.source_part.model_copy(update={"role": "justification"})}
    )
    item = change_meta(
        item,
        prepared_input_receipt=receipt,
        selection=item.intent.meta.selection.model_copy(update={"parts": (part,)}),
    )
    result = checked(item)
    assert result.evidence.status == "quote_present" and result.status == "unknown"
    assert result.reason == "source_binding_mismatch"


def test_repeated_source_quote_never_gets_an_input_coordinate() -> None:
    item = case()
    document = source(QUOTE + "\n" + QUOTE)
    result = validate_key_change_input(
        item.record, evidence(document), (document,), intent=item.intent
    )
    assert result.evidence.status == "quote_ambiguous"
    assert result.status == "unknown" and result.input_span is None


def test_result_rejects_incoherent_status_reason_and_coordinates() -> None:
    result = checked(case())
    with pytest.raises(ValueError, match="Only retained"):
        replace(result, status="omitted")
    with pytest.raises(ValueError, match="reason"):
        replace(result, reason="source_map_mismatch")
    with pytest.raises(ValueError, match="source length"):
        replace(result, input_span=(0, 1))
    with pytest.raises(ValueError, match="integer"):
        replace(result, input_span=(True, 2))

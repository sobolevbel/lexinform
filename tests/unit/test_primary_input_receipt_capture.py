import hashlib
from dataclasses import replace
from typing import Literal
from unittest.mock import patch

import pytest

from lexinform.models import (
    AnalysisQuestion,
    ArtifactIdentity,
    BatchIntent,
    BatchItemMeta,
    BatchRequest,
    DocumentPart,
    DocumentSelection,
    DocumentSelectionRecord,
    TextDocument,
)
from lexinform.models.documents import DocumentRole
from lexinform.sections import PAGE_BREAK
from lexinform.services.document_history import DocumentSourceRead, PhysicalSourceRead
from lexinform.services.input_receipts import (
    capture_loose_rcl_input_receipt,
    capture_primary_input_receipt,
    enrich_primary_input_intent,
    prepare_paired_archive_input_receipt,
    prepare_primary_input_receipt,
    with_block_companion,
)
from lexinform.text_projection import SourcePiece, project_pieces
from tests.unit.test_input_receipts import NOW, SOURCE, meta, request
from tests.unit.test_physical_extraction import docx, extractor


def captured(source_text: str = SOURCE * 20) -> tuple[DocumentSelection, DocumentSourceRead]:
    data = docx(source_text)
    observed = extractor().extract_document_with_provenance(data)
    text = observed.document.text
    body = ArtifactIdentity(sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data))
    selected = DocumentSelection(
        parts=(
            DocumentPart(
                role="bill",
                text=text,
                sha256=body.sha256,
                text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            ),
        )
    )
    source = DocumentSourceRead(
        TextDocument(url="https://example.test/actual.docx", kind="print"),
        DocumentSelectionRecord.from_selection(selected),
        (body,),
        extractions=(observed.result,),
    )
    return selected, source


def test_mixed_loose_capture_requires_its_actual_shape_and_every_parent() -> None:
    roles: tuple[DocumentRole, ...] = (
        "bill",
        "normative_annex",
        "self_amendment",
        "self_amendment",
        "justification",
        "osr",
    )
    parts: list[DocumentPart] = []
    parents: list[DocumentSourceRead] = []
    offset = 0
    pieces: list[SourcePiece] = []
    for index, role in enumerate(roles):
        loaded, parent = captured(f"Fragment {index}. {SOURCE * 20}")
        part = loaded.parts[0]
        parts.append(
            part.model_copy(
                update={
                    "url": f"https://example.test/actual-{index}.docx",
                    "role": role,
                    "text_end": len(part.text),
                }
            )
        )
        parents.append(parent)
        pieces.append(SourcePiece(part.text, offset))
        offset += len(part.text) + len(PAGE_BREAK)
    selection = DocumentSelection(parts=tuple(parts))
    source = DocumentSourceRead(
        TextDocument(
            url=parts[0].url,
            kind="rcl",
            normative_urls=(parts[1].url,),
            amendment_urls=(parts[2].url, parts[3].url),
            extra_urls=(parts[4].url, parts[5].url),
        ),
        DocumentSelectionRecord.from_selection(selection),
        tuple(artifact for parent in parents for artifact in parent.artifacts),
        extractions=tuple(extraction for parent in parents for extraction in parent.extractions),
    )
    original = PAGE_BREAK.join(part.text for part in parts)
    projection = project_pieces(original, pieces, separator=PAGE_BREAK)
    template = request(projection.text)
    assert isinstance(template.question, AnalysisQuestion), "the fixture carries a primary request"
    ctx = template.question.ctx.model_copy(
        update={"source_kind": "rcl", "text_source": "documents"}
    )
    frozen = BatchRequest(
        custom_id=template.custom_id,
        term=template.term,
        number=template.number,
        question=AnalysisQuestion(kind="analysis", ctx=ctx),
    ).freeze(
        payload_json=ctx.model_dump_json(),
        model="gpt-6.1-sol",
        prompt_version="pinned",
        estimated_cost_usd=0.01,
    )

    receipt = capture_loose_rcl_input_receipt(
        frozen, selection, projection, source, with_amendments=True
    )

    assert receipt is not None, "the native profile can retain every exact source parent"
    assert receipt.profile == "primary-loose-rcl-mixed-text-v1"
    assert tuple(part.role for part in receipt.source_parts) == roles
    assert receipt.source_selection_id == source.selection.selection_id
    prepared = prepare_primary_input_receipt(
        frozen,
        selection,
        projection,
        source,
        rcl_projection="annexes_and_amendments",
        now=lambda: pytest.fail("the loose RCL route does not need the clock"),
    )
    assert prepared.receipt == with_block_companion(
        receipt, source, projection.text, selection=selection
    )
    assert prepared.diagnostic is None and prepared.paired_archive_input is None
    assert capture_loose_rcl_input_receipt(frozen, selection, projection, source) is None
    incomplete = replace(source, extractions=source.extractions[:-1])
    assert (
        capture_loose_rcl_input_receipt(
            frozen, selection, projection, incomplete, with_amendments=True
        )
        is None
    )


@pytest.mark.parametrize("profile", ["admitted", "physical", "unknown"])
def test_only_this_captured_read_supplies_available_parent_refs(profile: str) -> None:
    selected, original = captured()
    source = (
        original
        if profile == "admitted"
        else PhysicalSourceRead(original.artifacts, extractions=original.extractions)
        if profile == "physical"
        else None
    )
    text = selected.parts[0].text
    receipt = capture_primary_input_receipt(
        request(text), selected, project_pieces(text, (SourcePiece(text, 0),)), source
    )
    assert receipt is not None, "one actual loose source part is supported"
    assert (receipt.source_selection_id is not None) == (profile == "admitted")
    assert (receipt.artifact_sha256 is not None) == (profile != "unknown")
    assert (receipt.extraction_id is not None) == (profile != "unknown")
    if profile == "admitted":
        assert receipt.source_selection_id == original.selection.selection_id
    assert receipt.retention == "complete" and receipt.input_completeness == "not_asserted"


@pytest.mark.parametrize("parent", ["body", "extraction"])
def test_ambiguous_captured_parent_is_unknown_instead_of_first_match(parent: str) -> None:
    selected, source = captured()
    if parent == "body":
        source = replace(
            source,
            artifacts=(
                *source.artifacts,
                source.artifacts[0].model_copy(
                    update={"byte_size": source.artifacts[0].byte_size + 1}
                ),
            ),
        )
    else:
        first = source.extractions[0]
        changed = first.model_copy(update={"package_policy_source_sha256": "e" * 64})
        source = replace(source, extractions=(first, changed))
    text = selected.parts[0].text
    receipt = capture_primary_input_receipt(request(text), selected, None, source)
    assert receipt is not None, "ambiguous optional parents do not hide an otherwise valid input"
    assert receipt.extraction_id is None
    assert (receipt.artifact_sha256 is None) == (parent == "body")


@pytest.mark.parametrize("profile", ["package", "explanation", "scan_label"])
def test_unsupported_request_profiles_do_not_claim_a_text_receipt(profile: str) -> None:
    selected, source = captured()
    frozen = request(selected.parts[0].text)
    assert isinstance(frozen.question, AnalysisQuestion), "request fixture uses a primary question"
    if profile == "package":
        selected = selected.model_copy(
            update={"parts": (selected.parts[0].model_copy(update={"members": ("actual.docx",)}),)}
        )
    else:
        kind: Literal["analysis", "explanation"] = (
            "explanation" if profile == "explanation" else "analysis"
        )
        ctx = frozen.question.ctx
        if profile == "scan_label":
            ctx = ctx.model_copy(update={"text_source": "scan"})
        frozen = frozen.model_copy(update={"question": AnalysisQuestion(kind=kind, ctx=ctx)})
    assert capture_primary_input_receipt(frozen, selected, None, source) is None
    prepared = prepare_primary_input_receipt(
        frozen, selected, None, source, rcl_projection=None, now=lambda: NOW
    )
    assert prepared.receipt is None and prepared.paired_archive_input is None
    assert prepared.diagnostic == "profile_or_proof_unavailable"


def test_optional_block_capture_failure_preserves_the_exact_receipt() -> None:
    selected, source = captured()
    frozen = request(selected.parts[0].text)
    original = capture_primary_input_receipt(frozen, selected, None, source)

    with patch(
        "lexinform.services.input_receipts.with_block_companion",
        side_effect=ValueError("optional proof unavailable"),
    ):
        prepared = prepare_primary_input_receipt(
            frozen, selected, None, source, rcl_projection=None, now=lambda: NOW
        )

    assert original is not None, "the single native text source can supply a base receipt"
    assert prepared.receipt == original
    assert prepared.diagnostic == "capture_failed"


def test_paired_capture_reports_unavailable_parent_before_mapping() -> None:
    selected, source = captured()
    text = selected.parts[0].text
    projection = project_pieces(text, (SourcePiece(text, 0),))
    assert (
        prepare_paired_archive_input_receipt(request(text), selected, projection, source, ())
        == "parents_unavailable"
    )


def test_optional_enrichment_keeps_original_intent_payload_binding_and_cost() -> None:
    selected, source = captured()
    frozen = request(selected.parts[0].text)
    receipt = capture_primary_input_receipt(frozen, selected, None, source)
    assert receipt is not None, "the fixture supplies a supported primary text input"
    original = BatchIntent.from_request(
        request=frozen, meta=meta(), provider="openai", created_at=NOW
    )
    enriched = enrich_primary_input_intent(original, receipt)
    assert enriched.request == original.request and enriched.created_at == original.created_at
    assert isinstance(enriched.meta, BatchItemMeta), (
        "enrichment preserves the original primary metadata"
    )
    assert enriched.meta.model_copy(update={"prepared_input_receipt": None}) == original.meta
    assert enriched.meta.input_binding == original.meta.input_binding
    assert original.meta.prepared_input_receipt is None
    with pytest.raises(ValueError):
        enrich_primary_input_intent(
            original, receipt.model_copy(update={"request_sha256": "f" * 64})
        )

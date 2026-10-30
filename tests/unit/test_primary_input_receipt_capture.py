import hashlib
from dataclasses import replace
from typing import Literal

import pytest

from lexinform.models import (
    AnalysisQuestion,
    ArtifactIdentity,
    BatchIntent,
    BatchItemMeta,
    DocumentPart,
    DocumentSelection,
    DocumentSelectionRecord,
    TextDocument,
)
from lexinform.services.document_history import DocumentSourceRead, PhysicalSourceRead
from lexinform.services.input_receipts import (
    capture_primary_input_receipt,
    enrich_primary_input_intent,
)
from lexinform.text_projection import SourcePiece, project_pieces
from tests.unit.test_input_receipts import NOW, SOURCE, meta, request
from tests.unit.test_physical_extraction import docx, extractor


def captured() -> tuple[DocumentSelection, DocumentSourceRead]:
    data = docx(SOURCE * 20)
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

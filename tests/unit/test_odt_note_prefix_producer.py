import json
from unittest.mock import patch

import pytest

from lexinform import extraction_records
from lexinform.models import DocumentSelection, ExtractedDocument, OdtNotedParts
from lexinform.models.extractions import ExtractionResult
from tests.unit.test_odt_blocks import odt_document
from tests.unit.test_odt_note_capture import note
from tests.unit.test_odt_note_status_producer import raw
from tests.unit.test_physical_extraction import archive, extractor


def status_only(data: bytes) -> ExtractionResult:
    with patch.object(
        extraction_records, "_with_odt_note_prefix", side_effect=RuntimeError("optional fault")
    ):
        return extractor().extract_document_with_provenance(data).result


def test_native_selected_prefix_uses_actual_original_block_and_keeps_old_metadata() -> None:
    data = raw()
    reader = extractor()
    old = status_only(data)
    physical = reader.extract_parts_with_odt_notes(data)
    with patch.object(extraction_records, "OdtNotedParts", wraps=OdtNotedParts) as binding:
        observed = reader.extract_document_with_provenance(data)
    assert binding.call_count == 1
    assert observed.document == reader.extract_document(data)
    assert observed.result.physical_manifest_sha256 == old.physical_manifest_sha256
    actual = observed.result.selected_parts[0]
    stored, status = actual.odt_note_prefix, actual.odt_note_status
    assert stored is not None and status is not None, (
        "native selected note has known capture and prefix"
    )
    source = physical.odt_notes[0].extraction
    assert (
        stored.origins == source
        and status.retained_manifest_sha256 == source.retained_manifest_sha256
    )
    bounds = {block.path: block for block in physical.legacy.odt_blocks[0].extraction.blocks}
    assert all(
        w.chars == len(bounds[w.path].text) and w.kind == bounds[w.path].kind for w in stored.blocks
    )
    assert actual.model_dump(exclude={"odt_note_status", "odt_note_prefix"}) == old.selected_parts[
        0
    ].model_dump(exclude={"odt_note_status"})
    old_status = old.selected_parts[0].odt_note_status
    assert old_status is not None, "baseline contains validated original capture status"
    assert status.model_dump(exclude={"contents"}) == old_status.model_dump(exclude={"contents"})
    assert (
        ExtractionResult.model_validate_json(observed.result.model_dump_json()) == observed.result
    )


def test_four_kib_prefix_keeps_original_counts_but_loses_forward_resolution_honestly() -> None:
    data = odt_document(
        "<text:p>"
        + "USTAWA Art. 1 Cudzoziemiec. " * 20
        + '<text:note-ref text:ref-name="n23">ref</text:note-ref>'
        + "".join(note(f"n{i}") for i in range(24))
        + "</text:p>"
    )
    actual = extractor().extract_document_with_provenance(data).result.selected_parts[0]
    stored, status = actual.odt_note_prefix, actual.odt_note_status
    assert stored is not None and status is not None, "large native capture retains a useful prefix"
    assert (
        len(json.dumps(stored.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":")))
        <= 4096
    )
    assert len(stored.origins.entries) < status.retained_notes
    assert stored.origins.note_count == status.observed_notes == 24
    assert stored.origins.coverage == "partial"
    assert (
        stored.origins.calls[0].association == "unverified"
        and stored.origins.calls[0].target_path is None
    )
    assert stored.origins.retained_manifest_sha256 != status.retained_manifest_sha256


@pytest.mark.parametrize("budget", ["prefix", "result"])
def test_no_useful_fit_preserves_entire_old_summary_and_status(
    monkeypatch: pytest.MonkeyPatch, budget: str
) -> None:
    data = raw()
    before = status_only(data)
    if budget == "prefix":
        monkeypatch.setattr(extraction_records, "MAX_ODT_NOTE_PREFIX_BYTES", 1)
    else:
        monkeypatch.setattr(
            extraction_records,
            "MAX_EXTRACTION_METADATA_BYTES",
            len(before.model_dump_json().encode()),
        )
    assert extractor().extract_document_with_provenance(data).result == before


@pytest.mark.parametrize("failure", [RuntimeError("metadata"), KeyboardInterrupt("cancel")])
def test_only_new_optional_enrichment_is_fail_open(failure: BaseException) -> None:
    data = raw()
    before = status_only(data)
    with patch.object(extraction_records, "OdtNotePrefix", side_effect=failure):
        if isinstance(failure, Exception):
            assert extractor().extract_document_with_provenance(data).result == before
        else:
            with pytest.raises(KeyboardInterrupt) as caught:
                extractor().extract_document_with_provenance(data)
            assert caught.value is failure


def test_duplicate_input_bodies_keep_separate_physical_indices_and_aggregate_budget() -> None:
    data = archive({"a.odt": raw(), "b.odt": raw(), "c.odt": raw()})
    reader = extractor()
    physical = reader.extract_parts_with_odt_notes(data)
    selected = ExtractedDocument(
        text="\n".join(p.text for p in physical.legacy.parts),
        selection=DocumentSelection(parts=physical.legacy.parts),
    )
    reference = reader.extract_document_with_provenance(data).result
    value = extraction_records.summarize_extraction(
        physical.legacy,
        selected,
        components=reference.components,
        options=reference.options,
        odt_origins=physical.odt_notes,
    )
    prefixes = [p.odt_note_prefix for p in value.selected_parts if p.odt_note_prefix is not None]
    assert prefixes and len({p.sha256 for p in value.selected_parts}) == 1
    assert all(
        p.odt_note_prefix is None or p.physical_index == p.odt_note_prefix.physical_index
        for p in value.selected_parts
    )
    assert (
        sum(
            len(json.dumps(p.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":")))
            for p in prefixes
        )
        <= 4096
    )
    assert len(value.selected_parts) == 3
    assert len(value.model_dump_json().encode()) <= 16384

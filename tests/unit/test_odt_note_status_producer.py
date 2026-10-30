from datetime import timedelta
from types import MethodType
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest

from lexinform import extraction_records
from lexinform.adapters import document_text
from lexinform.adapters.document_text import DocumentTextExtractor, OdtTextExtractor
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.document_candidates import select_document_parts
from lexinform.models import DocumentPart, DocumentSelection, ExtractedDocument, ExtractedParts
from lexinform.models.extractions import ExtractionResult
from lexinform.services.document_history import PhysicalSourceRead, record_source_read
from tests.fakes import FakeBatchBackend
from tests.unit.test_input_receipts import request
from tests.unit.test_odt_blocks import odt_document
from tests.unit.test_odt_note_capture import note
from tests.unit.test_physical_callers import NOW, URL, loader_for
from tests.unit.test_physical_extraction import archive, extractor


def raw() -> bytes:
    return odt_document("<text:p>" + "USTAWA Art. 1. Cudzoziemiec. " * 20 + note() + "</text:p>")


def summarize(
    physical: ExtractedParts,
    selected: ExtractedDocument,
    observed: ExtractionResult,
    **kwargs: object,
) -> ExtractionResult:
    return extraction_records.summarize_extraction(
        physical, selected, components=observed.components, options=observed.options
    )


def test_default_provenance_keeps_text_selection_physical_identity_and_frozen_fake_payload() -> (
    None
):
    reader = extractor()
    data = raw()
    physical = reader.extract_parts(data)
    selected = reader.extract_document(data)
    observed = reader.extract_document_with_provenance(data)
    baseline = summarize(physical, selected, observed.result)
    assert observed.document == selected
    assert observed.result.physical_manifest_sha256 == baseline.physical_manifest_sha256
    status = observed.result.selected_parts[0].odt_note_status
    assert status is not None, "native provenance has the actual same-pass ODT companion"
    assert status.observed_notes == status.observed_calls == 1
    assert status.contents == "not_retained" and status.xml_completeness == "not_asserted"
    assert observed.result.selected_parts[0].limitations == baseline.selected_parts[0].limitations
    assert len(observed.result.model_dump_json().encode()) <= 16384
    backend = FakeBatchBackend()
    before = request(selected.text).model_copy(
        update={"payload_json": None, "payload_sha256": None}
    )
    after = request(observed.document.text).model_copy(
        update={"payload_json": None, "payload_sha256": None}
    )
    assert (
        backend.prepare_request(before).payload_json == backend.prepare_request(after).payload_json
    )
    assert (
        backend.prepare_request(before).payload_sha256
        == backend.prepare_request(after).payload_sha256
    )


def test_duplicate_body_hashes_only_use_the_actual_physical_index() -> None:
    data = archive({"a.odt": raw(), "b.odt": raw()})
    reader = extractor()
    linked = reader.extract_parts_with_odt_notes(data)
    selected = ExtractedDocument(
        text="\n".join(p.text for p in linked.legacy.parts),
        selection=DocumentSelection(parts=linked.legacy.parts),
    )
    reference = reader.extract_document_with_provenance(data).result
    result = extraction_records.summarize_extraction(
        linked.legacy,
        selected,
        components=reference.components,
        options=reference.options,
        odt_origins=(linked.odt_notes[1],),
    )
    assert result.selected_parts[0].sha256 == result.selected_parts[1].sha256
    assert (
        result.selected_parts[0].odt_note_status is None
        and result.selected_parts[1].odt_note_status is not None
    )


@pytest.mark.parametrize("failure", [RuntimeError("metadata fault"), KeyboardInterrupt("cancel")])
def test_only_optional_enrichment_is_fail_open(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    data = raw()
    reader = extractor()
    physical = reader.extract_parts(data)
    selected = select_document_parts(physical, max_part_chars=None)
    reference = reader.extract_document_with_provenance(data).result
    baseline = summarize(physical, selected, reference)

    def fail(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(extraction_records, "OdtNoteStatus", fail)
    if isinstance(failure, Exception):
        observed = reader.extract_document_with_provenance(data)
        assert observed.result == baseline and observed.document == selected
    else:
        with pytest.raises(KeyboardInterrupt) as caught:
            reader.extract_document_with_provenance(data)
        assert caught.value is failure


def test_original_selection_exception_object_and_cause_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = ValueError("original selection")
    cause = RuntimeError("cause")

    def fail(*args: object, **kwargs: object) -> None:
        raise failure from cause

    monkeypatch.setattr(document_text, "select_document_parts", fail)
    with pytest.raises(ValueError) as caught:
        extractor().extract_document_with_provenance(raw())
    assert caught.value is failure and caught.value.__cause__ is cause


def test_existing_part_prefix_survives_dropping_new_diagnostics_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = archive({f"{i:02}.odt": raw() for i in range(16)})
    reader = extractor()
    linked = reader.extract_parts_with_odt_notes(data)
    selected = ExtractedDocument(
        text="\n".join(p.text for p in linked.legacy.parts),
        selection=DocumentSelection(parts=linked.legacy.parts),
    )
    reference = reader.extract_document_with_provenance(data).result
    baseline = summarize(linked.legacy, selected, reference)
    monkeypatch.setattr(
        extraction_records,
        "MAX_EXTRACTION_METADATA_BYTES",
        len(baseline.model_dump_json().encode()) + 100,
    )
    result = extraction_records.summarize_extraction(
        linked.legacy,
        selected,
        components=reference.components,
        options=reference.options,
        odt_origins=linked.odt_notes,
    )
    assert len(result.selected_parts) == len(baseline.selected_parts) == 16
    assert result == baseline


def test_class_and_instance_custom_physical_boundaries_stay_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom = ExtractedParts(
        input_sha256="a" * 64, parts=(DocumentPart(text="custom actual physical", sha256="a" * 64),)
    )

    def override(self: DocumentTextExtractor, data: bytes, **kwargs: object) -> ExtractedParts:
        return custom

    reader = extractor()
    monkeypatch.setattr(reader, "extract_parts", MethodType(override, reader))
    assert (
        reader.extract_document_with_provenance(raw()).result.selected_parts[0].odt_note_status
        is None
    )
    monkeypatch.undo()
    monkeypatch.setattr(DocumentTextExtractor, "extract_parts", override)
    observed = extractor().extract_document_with_provenance(raw())
    assert observed.document.text == "custom actual physical"
    assert all(p.odt_note_status is None for p in observed.result.selected_parts)


def test_cache_repeat_after_365days_reuses_original_status_and_creates_no_rows(
    repo: SqliteBillRepository,
) -> None:
    loader, gateway = loader_for(raw())
    first = loader.read(URL)
    source = PhysicalSourceRead(
        artifacts=loader.read_artifacts(), extractions=loader.read_extraction_results()
    )
    record_source_read(
        repo, source, term=10, number="controlled", purpose="analysis", recorded_at=NOW
    )
    saved = repo.dump()
    before = loader.read_extraction_results()
    assert before[0].selected_parts[0].odt_note_status is not None, (
        "actual native metadata was captured initially"
    )
    loader.reset_read_metrics()
    loader.reset_download_diagnostics()
    assert loader.read(URL) == first and loader.read_extraction_results() == before
    record_source_read(
        repo,
        source,
        term=10,
        number="controlled",
        purpose="analysis",
        recorded_at=NOW + timedelta(days=365),
    )
    assert repo.dump() == saved and gateway.calls == [f"download:{URL}"]
    repo.restore(saved)
    assert repo.dump() == saved


def test_legacy_cache_does_not_invent_status_after_new_native_capability_returns(
    monkeypatch: pytest.MonkeyPatch, repo: SqliteBillRepository
) -> None:
    original = OdtTextExtractor.extract_with_note_origins

    def legacy(self: OdtTextExtractor, data: bytes) -> tuple[object, None]:
        return self.extract_odt_blocks(data), None

    monkeypatch.setattr(OdtTextExtractor, "extract_with_note_origins", legacy)
    loader, gateway = loader_for(raw())
    first = loader.read(URL)
    before = loader.read_extraction_results()
    assert before[0].selected_parts[0].odt_note_status is None
    monkeypatch.setattr(OdtTextExtractor, "extract_with_note_origins", original)
    loader.reset_read_metrics()
    assert loader.read(URL) == first and loader.read_extraction_results() == before
    assert gateway.calls == [f"download:{URL}"]


def test_default_native_still_reads_and_parses_xml_once() -> None:
    parser = ET.fromstring
    read = document_text._read_xml
    with (
        patch.object(ET, "fromstring", wraps=parser) as parsed,
        patch.object(document_text, "_read_xml", wraps=read) as loaded,
    ):
        extractor().extract_document_with_provenance(raw())
    assert parsed.call_count == loaded.call_count == 1

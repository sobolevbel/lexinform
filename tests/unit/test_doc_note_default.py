"""Default DOC diagnostics reuse native extraction and preserve selection and byte budgets."""

import hashlib
from unittest.mock import patch

import olefile

from lexinform.extraction_records import summarize_extraction
from lexinform.models.extractions import ExtractionResult
from tests.unit.test_doc_note_extraction import source_notes
from tests.unit.test_doc_note_physical_parts import CustomDoc, extractor, ole_document
from tests.unit.test_physical_extraction import archive


def test_default_native_diagnostics_read_the_ole_container_once() -> None:
    data = ole_document(*source_notes())
    with patch("olefile.OleFileIO", wraps=olefile.OleFileIO) as opened:
        result = extractor().extract_document_with_provenance(data)

    assert opened.call_count == 1
    notes = result.result.selected_parts[0].doc_notes
    assert notes is not None, "A supported native note source is retained by default"
    assert notes.state == "checked" and notes.observed_links == 1
    assert notes.input_sha256 == hashlib.sha256(data).hexdigest()


def test_default_and_legacy_entrypoints_keep_exactly_the_same_selected_document() -> None:
    data = ole_document(*source_notes())
    reader = extractor()

    result = reader.extract_document_with_provenance(data)
    legacy = reader.extract_document(data)

    assert result.document == legacy
    assert result.result.output_text_sha256 == hashlib.sha256(legacy.text.encode()).hexdigest()
    assert result.document.selection == legacy.selection


def test_invalid_optional_markers_retain_the_legacy_document_with_unknown_source_state() -> None:
    data = ole_document(*source_notes(main="AxB\r"))
    reader = extractor()

    result = reader.extract_document_with_provenance(data)

    assert result.document == reader.extract_document(data)
    notes = result.result.selected_parts[0].doc_notes
    assert notes is not None, "Native unqualified notes retain explicit unavailable state"
    assert notes.state == "unavailable"
    assert notes.observed_links is None
    assert not notes.links


def test_custom_doc_extractors_do_not_gain_native_source_claims() -> None:
    data = ole_document(*source_notes())
    reader = extractor(CustomDoc())

    result = reader.extract_document_with_provenance(data)

    assert result.document == reader.extract_document(data)
    assert result.result.selected_parts[0].doc_notes is None
    assert "doc_notes" not in result.result.model_dump_json()


def test_default_summaries_keep_the_existing_duplicate_file_selection() -> None:
    data = ole_document(*source_notes())
    bundle = archive({"a.doc": data, "nested.zip": archive({"b.doc": data})})
    reader = extractor()

    result = reader.extract_document_with_provenance(bundle)

    assert result.document == reader.extract_document(bundle)
    summaries = result.result.selected_parts
    assert result.result.physical_part_count == 2
    assert [part.physical_index for part in summaries] == [0]
    assert all(part.doc_notes is not None for part in summaries)
    assert all(part.sha256 == hashlib.sha256(data).hexdigest() for part in summaries)
    assert result.result.artifact_sha256 == hashlib.sha256(bundle).hexdigest()


def test_many_default_summaries_omit_new_diagnostics_before_existing_rows() -> None:
    main = "USTAWA\rArt. 1. Cudzoziemiec sklada wniosek.\rA\x02B\r"
    files = {"projekt.doc": ole_document(*source_notes(main=main, reference_cp=len(main) - 3))}
    for index in range(19):
        main = f"Załącznik nr {index} do ustawy\rDane {index}\rA\x02B\r"
        files[f"zalacznik-{index}.doc"] = ole_document(
            *source_notes(main=main, reference_cp=len(main) - 3)
        )
    bundle = archive(files)
    reader = extractor()

    result = reader.extract_document_with_provenance(bundle)

    assert result.document == reader.extract_document(bundle)
    assert result.result.selected_part_count == 20
    assert len(result.result.selected_parts) == 16
    assert result.result.omitted_selected_parts == 4
    notes = [part.doc_notes for part in result.result.selected_parts if part.doc_notes is not None]
    assert notes and len(notes) < 16
    assert sum(len(note.model_dump_json().encode()) for note in notes) <= 4096
    assert len(result.result.model_dump_json().encode()) <= 16384
    assert ExtractionResult.model_validate_json(result.result.model_dump_json()) == result.result


def test_new_diagnostics_do_not_change_the_existing_physical_manifest() -> None:
    data = ole_document(*source_notes())
    reader = extractor()
    result = reader.extract_document_with_provenance(data)
    baseline = summarize_extraction(
        reader.extract_parts(data),
        result.document,
        components=result.result.components,
        options=result.result.options,
    )

    assert result.result.physical_manifest_sha256 == baseline.physical_manifest_sha256
    assert result.result.output_text_sha256 == baseline.output_text_sha256
    assert result.result.selected_part_count == baseline.selected_part_count
    assert result.result.omitted_selected_parts == baseline.omitted_selected_parts

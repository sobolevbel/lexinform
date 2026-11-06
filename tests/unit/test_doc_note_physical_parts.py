"""Binary DOC note metadata binds to physical leaves without changing their legacy projection."""

import datetime as dt
import hashlib
import json
import sqlite3
import struct
from contextlib import closing
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError
from tools.source_history_doctor import audit

from lexinform import extraction_records
from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.extraction_records import summarize_doc_notes, summarize_extraction
from lexinform.models import ArtifactMetadata
from lexinform.models.doc_notes import (
    DocNoteCapture,
    DocNotedParts,
    DocNoteSummary,
)
from lexinform.models.documents import DocumentSelection, ExtractedDocument
from lexinform.models.extractions import ExtractionComponent, ExtractionOptions, ExtractionResult
from tests.fakes import FakeTextExtractor
from tests.unit.test_doc_note_extraction import source_notes
from tests.unit.test_physical_extraction import archive
from tests.unit.test_word_note_origins import package


def ole_document(word: bytes, table: bytes) -> bytes:
    free, end, fat_sector = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
    header = bytearray(512)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<6H", header, 24, 0x003E, 3, 0xFFFE, 9, 6, 0)
    struct.pack_into("<9I", header, 40, 0, 1, 0, 0, 4096, end, 0, end, 0)
    struct.pack_into("<109I", header, 76, 1, *([free] * 108))
    directory = bytearray(512)
    for index, name, kind, right, child, start, size in (
        (0, "Root Entry", 5, free, 1, end, 0),
        (1, "WordDocument", 2, 2, free, 2, 4096),
        (2, "1Table", 2, free, free, 10, 4096),
    ):
        entry = bytearray(128)
        encoded = (name + "\0").encode("utf-16-le")
        entry[: len(encoded)] = encoded
        struct.pack_into("<HBBIII", entry, 64, len(encoded), kind, 1, free, right, child)
        struct.pack_into("<IQ", entry, 116, start, size)
        directory[index * 128 : (index + 1) * 128] = entry
    fat = [free] * 128
    fat[0], fat[1] = end, fat_sector
    for first in (2, 10):
        for sector in range(first, first + 7):
            fat[sector] = sector + 1
        fat[first + 7] = end
    if len(word) > 4096 or len(table) > 4096:
        raise ValueError("Synthetic OLE fixture requires streams within one FAT sector")
    return (
        bytes(header + directory)
        + struct.pack("<128I", *fat)
        + word.ljust(4096, b"\0")
        + table.ljust(4096, b"\0")
    )


def extractor(doc: DocTextExtractor | None = None) -> DocumentTextExtractor:
    return DocumentTextExtractor(
        FakeTextExtractor(), FakeTextExtractor(), doc or DocTextExtractor()
    )


def captured(
    *, count: int = 1, state: Literal["checked", "unavailable"] = "checked"
) -> DocNoteCapture:
    return DocNoteCapture.model_validate(
        {
            "input_sha256": "a" * 64,
            "text_sha256": "b" * 64,
            "state": state,
            "table_name": "1Table" if state == "checked" else None,
            "main_characters": 1000 if state == "checked" else None,
            "footnote_characters": count * 10 + 1 if state == "checked" else None,
            "endnote_characters": 0 if state == "checked" else None,
            "validation": "source_characters_checked_cf_spec_unknown"
            if state == "checked"
            else None,
            "unavailable_reason": None if state == "checked" else "unsupported_or_invalid",
            "links": tuple(
                {
                    "kind": "footnote",
                    "reference_cp": index,
                    "note_start_cp": index * 10,
                    "note_end_cp": (index + 1) * 10,
                    "story_start_cp": 1000,
                    "numbering_flag": 1,
                }
                for index in range(count)
            )
            if state == "checked"
            else (),
        }
    )


def summary(physical: DocNotedParts) -> ExtractionResult:
    selected = ExtractedDocument(
        text="\n".join(part.text for part in physical.legacy.parts),
        selection=DocumentSelection(parts=physical.legacy.parts),
    )
    return summarize_extraction(
        physical.legacy,
        selected,
        components=(ExtractionComponent(role="package", qualified_name_sha256="c" * 64),),
        options=ExtractionOptions(
            max_member_bytes=None,
            max_part_chars=None,
            archive_depth=3,
            archive_bytes=100000,
            archive_members=10,
            xml_bytes=100000,
        ),
    )


def test_native_capture_binds_leaf_sha_and_preserves_the_physical_projection() -> None:
    data = ole_document(*source_notes())

    legacy = extractor().extract_parts(data)
    result = extractor().extract_parts_with_doc_notes(data)

    assert result.legacy == legacy
    assert result.doc_notes[0].part_index == 0
    source = result.doc_notes[0].extraction
    assert source.input_sha256 == hashlib.sha256(data).hexdigest() == result.legacy.parts[0].sha256
    assert source.text_sha256 == result.legacy.parts[0].text_sha256
    assert source.state == "checked"
    assert len(source.links) == 1
    assert DocNotedParts.model_validate_json(result.model_dump_json()) == result


def test_nested_duplicate_leaves_keep_distinct_input_positions() -> None:
    data = ole_document(*source_notes())
    bundle = archive({"a.doc": data, "nested.zip": archive({"b.doc": data})})

    result = extractor().extract_parts_with_doc_notes(bundle)

    assert [part.members for part in result.legacy.parts] == [("a.doc",), ("nested.zip", "b.doc")]
    assert [part.part_index for part in result.doc_notes] == [0, 1]
    assert result.doc_notes[0].extraction == result.doc_notes[1].extraction
    assert result.legacy.input_sha256 != result.doc_notes[0].extraction.input_sha256


def test_wrong_physical_sha_is_refused() -> None:
    result = extractor().extract_parts_with_doc_notes(ole_document(*source_notes()))
    payload = result.model_dump(mode="json")
    payload["doc_notes"][0]["extraction"]["input_sha256"] = "f" * 64

    with pytest.raises(ValidationError, match="physical input"):
        DocNotedParts.model_validate(payload)


def test_failed_optional_markers_keep_the_text_with_explicit_unknown_metadata() -> None:
    data = ole_document(*source_notes(main="AxB\r"))

    result = extractor().extract_parts_with_doc_notes(data)

    assert result.legacy == extractor().extract_parts(data)
    assert result.doc_notes[0].extraction.state == "unavailable"
    assert not result.doc_notes[0].extraction.links
    assert result.doc_notes[0].extraction.main_characters is None


class CustomDoc(DocTextExtractor):
    def extract(self, data: bytes) -> str:
        return "custom text"


def test_custom_extractors_keep_their_existing_string_behavior() -> None:
    data = ole_document(*source_notes())

    result = extractor(CustomDoc()).extract_parts_with_doc_notes(data)

    assert result.legacy.parts[0].text == "custom text"
    assert result.doc_notes == ()


def test_summary_reader_has_distinct_full_and_prefix_witnesses() -> None:
    full = captured(count=8)

    prefix = DocNoteSummary.from_capture(full, retained_links=2)
    complete = DocNoteSummary.from_capture(full, retained_links=8)

    assert prefix.coverage == "prefix"
    assert prefix.observed_links == 8
    assert len(prefix.links) == 2
    assert prefix.captured_manifest_sha256 != prefix.retained_manifest_sha256
    assert complete.coverage == "complete_declared_links"
    assert complete.captured_manifest_sha256 == complete.retained_manifest_sha256
    assert DocNoteSummary.model_validate_json(prefix.model_dump_json()) == prefix


def test_unknown_capture_does_not_claim_zero_observed_links() -> None:
    prefix = DocNoteSummary.from_capture(captured(state="unavailable"), retained_links=0)

    assert prefix.coverage == "unknown"
    assert prefix.observed_links is None


def test_summary_helper_enriches_without_changing_the_physical_manifest() -> None:
    physical = extractor().extract_parts_with_doc_notes(ole_document(*source_notes()))
    baseline = summary(physical)

    enriched = summarize_doc_notes(baseline, physical)

    assert enriched.physical_manifest_sha256 == baseline.physical_manifest_sha256
    assert enriched.output_text_sha256 == baseline.output_text_sha256
    assert enriched.selected_part_count == baseline.selected_part_count
    assert enriched.omitted_selected_parts == baseline.omitted_selected_parts
    assert enriched.selected_parts[0].doc_notes is not None
    assert "doc_notes" not in baseline.model_dump_json()
    assert ExtractionResult.model_validate_json(enriched.model_dump_json()) == enriched


@pytest.mark.parametrize("field", ["part_index", "text_sha256", "route"])
def test_wrong_physical_binding_is_refused(field: str) -> None:
    result = extractor().extract_parts_with_doc_notes(ole_document(*source_notes()))
    payload = result.model_dump(mode="json")
    if field == "part_index":
        payload["doc_notes"][0]["part_index"] = 1
    elif field == "text_sha256":
        payload["doc_notes"][0]["extraction"][field] = "f" * 64
    else:
        payload["legacy"]["unlocated_text"][0]["extraction"][field] = "word_xml"

    with pytest.raises(ValidationError):
        DocNotedParts.model_validate(payload)


@pytest.mark.parametrize("field", ["retained_manifest_sha256", "observed_links", "state"])
def test_invalid_retained_summary_is_refused(field: str) -> None:
    prefix = DocNoteSummary.from_capture(captured(count=8), retained_links=2)
    payload = prefix.model_dump(mode="json")
    payload[field] = (
        "f" * 64
        if field == "retained_manifest_sha256"
        else 1
        if field == "observed_links"
        else "unavailable"
    )

    with pytest.raises(ValidationError):
        DocNoteSummary.model_validate(payload)


def test_optional_doc_summary_is_retained_by_the_native_provenance_path() -> None:
    data = ole_document(*source_notes())

    result = extractor().extract_document_with_provenance(data)

    notes = result.result.selected_parts[0].doc_notes
    assert notes is not None, "The native DOC leaf yields source-checked metadata"
    assert notes.state == "checked" and notes.observed_links == 1
    assert notes.validation == "source_characters_checked_cf_spec_unknown"
    assert (
        notes.projections
        == DocTextExtractor().extract_with_notes(data, capture_projection=True).projections
    )


def test_default_doc_coordinates_leave_space_for_word_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    word_data = package("<w:p><w:ins><w:r><w:t>Added text</w:t></w:r></w:ins></w:p>", {})
    data = archive({"projekt.doc": ole_document(*source_notes()), "uzasadnienie.docx": word_data})
    reader = DocumentTextExtractor(FakeTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    enriched = reader.extract_document_with_provenance(data)
    payload = enriched.result.model_dump(mode="json")
    (doc,) = [part for part in payload["selected_parts"] if "doc_notes" in part]
    assert doc["doc_notes"].pop("projections")
    (word,) = [part for part in payload["selected_parts"] if "word_view_status" in part]
    assert word["word_view_status"].get("observed_inline") is not None
    expected = ExtractionResult.model_validate(payload)
    monkeypatch.setattr(
        extraction_records,
        "MAX_EXTRACTION_METADATA_BYTES",
        len(expected.model_dump_json().encode()),
    )

    bounded = reader.extract_document_with_provenance(data)

    assert bounded == enriched.model_copy(update={"result": expected})


def test_aggregate_budget_preserves_existing_selected_rows() -> None:
    data = ole_document(*source_notes())
    physical = extractor().extract_parts_with_doc_notes(
        archive({f"{index}.doc": data for index in range(10)})
    )
    source = captured(count=64)
    offered = tuple(
        part.model_copy(
            update={
                "extraction": source.model_copy(
                    update={
                        "input_sha256": part.extraction.input_sha256,
                        "text_sha256": part.extraction.text_sha256,
                    }
                )
            }
        )
        for part in physical.doc_notes
    )
    physical = DocNotedParts(legacy=physical.legacy, doc_notes=offered)
    baseline = summary(physical)

    result = summarize_doc_notes(baseline, physical)

    assert len(result.selected_parts) == len(baseline.selected_parts) == 10
    assert result.omitted_selected_parts == baseline.omitted_selected_parts == 0
    assert (
        sum(
            len(part.doc_notes.model_dump_json().encode())
            for part in result.selected_parts
            if part.doc_notes is not None
        )
        <= 4096
    )
    assert len(result.model_dump_json().encode()) <= 16384
    assert result.selected_parts[0].doc_notes is not None
    assert result.selected_parts[0].doc_notes.coverage == "prefix"
    assert any(part.doc_notes is None for part in result.selected_parts)
    assert summarize_doc_notes(baseline, physical) == result


def test_native_physical_capture_stops_at_its_optional_sidecar_cap() -> None:
    data = ole_document(*source_notes())
    bundle = archive({f"{index}.doc": data for index in range(65)})

    result = extractor().extract_parts_with_doc_notes(bundle)

    assert len(result.legacy.parts) == 65
    assert len(result.doc_notes) == 64
    assert result.legacy == extractor().extract_parts(bundle)


def test_constructed_summary_cannot_bypass_its_physical_source_binding() -> None:
    physical = extractor().extract_parts_with_doc_notes(ole_document(*source_notes()))
    result = summarize_doc_notes(summary(physical), physical)
    forged_part = result.selected_parts[0].model_copy(update={"sha256": "f" * 64})
    forged = result.model_copy(update={"selected_parts": (forged_part,)})

    with pytest.raises(ValidationError, match="actual source projection"):
        ExtractionResult.model_validate(forged)


def test_constructed_dictionary_is_not_a_typed_doc_note_summary() -> None:
    physical = extractor().extract_parts_with_doc_notes(ole_document(*source_notes()))
    result = summarize_doc_notes(summary(physical), physical)
    notes = result.selected_parts[0].doc_notes
    assert notes is not None, "The native DOC fixture produces a retained note summary"
    forged_part = result.selected_parts[0].model_copy(update={"doc_notes": notes.model_dump()})
    forged = result.model_copy(update={"selected_parts": (forged_part,)})

    with pytest.raises(ValidationError, match="typed note source"):
        ExtractionResult.model_validate(forged)


@pytest.mark.parametrize("route", ["constructed", "json"])
def test_a_proper_prefix_cannot_claim_the_full_captured_hash(route: str) -> None:
    prefix = DocNoteSummary.from_capture(captured(count=8), retained_links=2)
    forged = prefix.model_copy(update={"captured_manifest_sha256": prefix.retained_manifest_sha256})

    with pytest.raises(ValidationError, match="proper DOC prefix"):
        if route == "json":
            DocNoteSummary.model_validate_json(forged.model_dump_json())
        else:
            DocNoteSummary.model_validate(forged)


@pytest.mark.parametrize("known", [False, True])
def test_future_doc_summary_survives_sql_restore_and_corruption_is_atomic(
    tmp_path: Path, known: bool
) -> None:
    now = dt.datetime(2026, 11, 4, tzinfo=dt.UTC)
    data = ole_document(*source_notes(main="A\x02B\r" if known else "AxB\r"))
    physical = extractor().extract_parts_with_doc_notes(data)
    legacy = summary(physical)
    enriched = summarize_doc_notes(legacy, physical)
    notes = enriched.selected_parts[0].doc_notes
    assert notes is not None, "The native leaf yields checked or explicit unavailable metadata"
    assert notes.state == ("checked" if known else "unavailable")
    database = tmp_path / "source.db"
    source = SqliteBillRepository(database)
    source.migrate()
    with source.atomic():
        source.save_artifact_metadata(
            ArtifactMetadata(sha256=enriched.artifact_sha256, byte_size=len(data), recorded_at=now)
        )
        source.save_extraction_result(legacy, recorded_at=now)
        source.save_extraction_result(enriched, recorded_at=now)
    before = source.dump()
    source.close()
    target_path = tmp_path / "restored.db"
    target = SqliteBillRepository(target_path)
    target.restore(before)

    assert target.get_extraction_result(legacy.result_id) == legacy
    assert target.get_extraction_result(enriched.result_id) == enriched
    assert "doc_notes" not in legacy.model_dump_json()
    assert target.dump() == before
    broken = enriched.model_dump(mode="json")
    broken["selected_parts"][0]["doc_notes"]["retained_manifest_sha256"] = "f" * 64
    original = enriched.model_dump_json().replace("'", "''")
    assert before.count(original) == 1, "The source dump contains exactly one future extraction row"
    corrupted = before.replace(original, json.dumps(broken).replace("'", "''"), 1)

    with pytest.raises(ValueError):
        target.restore(corrupted)

    assert target.dump() == before
    target.close()
    result = audit(target_path)
    assert not [finding for finding in result["findings"] if finding["level"] == "error"]
    invalid_path = tmp_path / "invalid.db"
    with closing(sqlite3.connect(invalid_path)) as connection:
        connection.executescript(corrupted)
    invalid = audit(invalid_path)
    assert invalid["summary"]["extraction_rows"] == 2
    assert [
        finding for finding in invalid["findings"] if finding["code"] == "invalid_extraction_result"
    ] == [
        {
            "code": "invalid_extraction_result",
            "ref": f"extraction_results/{enriched.result_id}",
            "level": "error",
        }
    ]


@pytest.mark.parametrize("control", ["exact", "unknown_ref", "duplicate", "too_far", "empty_hash"])
def test_doc_projection_reader_binds_offsets_to_retained_links_and_text(control: str) -> None:
    physical = extractor().extract_parts_with_doc_notes(ole_document(*source_notes()))
    baseline = summarize_doc_notes(summary(physical), physical)
    payload = baseline.model_dump(mode="json")
    part = payload["selected_parts"][0]
    notes = part["doc_notes"]
    link = notes["links"][0]
    body = {"start": 3, "end": 7, "text_sha256": hashlib.sha256(b"note").hexdigest()}
    position = {"reference_cp": link["reference_cp"], "reference_offset": 1, "body": body}
    notes["projections"] = [position]
    if control == "unknown_ref":
        position["reference_cp"] = link["reference_cp"] + 1
    elif control == "duplicate":
        notes["projections"].append(position.copy())
    elif control == "too_far":
        position["reference_offset"] = part["text_chars"] + 1
    elif control == "empty_hash":
        body["end"] = body["start"]

    if control == "exact":
        result = ExtractionResult.model_validate(payload)
        assert ExtractionResult.model_validate_json(result.model_dump_json()) == result
        assert "projections" not in baseline.model_dump_json()
        result_notes = result.selected_parts[0].doc_notes
        baseline_notes = baseline.selected_parts[0].doc_notes
        assert result_notes is not None and baseline_notes is not None, "the fixture retains notes"
        assert result_notes.retained_manifest_sha256 == baseline_notes.retained_manifest_sha256
    else:
        with pytest.raises(ValidationError):
            ExtractionResult.model_validate(payload)


@pytest.mark.parametrize(
    ("main", "reference_cp", "reference_offset"),
    [("😀\x02B\r", 2, 1), ("A\x13\x02\x15B\r", 2, None)],
)
def test_native_doc_projection_tracks_decoded_characters_and_visible_references(
    main: str, reference_cp: int, reference_offset: int | None
) -> None:
    data = ole_document(*source_notes(main=main, reference_cp=reference_cp, note="\x02😀note\r\r"))
    reader = DocTextExtractor()
    baseline = reader.extract_with_notes(data)

    observed = reader.extract_with_notes(data, capture_projection=True)

    assert observed.result == baseline.result
    assert observed.input_sha256 == baseline.input_sha256
    assert not baseline.projections
    (projection,) = observed.projections
    assert projection.reference_cp == reference_cp
    assert projection.reference_offset == reference_offset
    body = projection.body
    assert body is not None, "the complete synthetic note projects into the cleaned text"
    assert observed.result.legacy_text[body.start : body.end] == "😀note"
    assert body.text_sha256 == hashlib.sha256("😀note".encode()).hexdigest()


@pytest.mark.parametrize("limit", ["none", "doc", "result"])
def test_doc_physical_projection_preserves_existing_note_metadata_under_caps(
    monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    data = ole_document(*source_notes())
    reader = extractor()
    legacy = reader.extract_parts_with_doc_notes(data)
    captured = reader.extract_parts_with_doc_notes(data, capture_projection=True)
    baseline = summarize_doc_notes(summary(legacy), legacy)
    legacy_notes = baseline.selected_parts[0].doc_notes
    assert legacy_notes is not None, "the fixture captures a DOC link"
    assert captured.legacy == legacy.legacy
    assert not legacy.doc_notes[0].projections and captured.doc_notes[0].projections
    if limit == "doc":
        monkeypatch.setattr(
            extraction_records,
            "MAX_DOC_NOTE_SUMMARY_BYTES",
            len(legacy_notes.model_dump_json().encode()),
        )
    elif limit == "result":
        monkeypatch.setattr(
            extraction_records,
            "MAX_EXTRACTION_METADATA_BYTES",
            len(baseline.model_dump_json().encode()),
        )

    enriched = summarize_doc_notes(summary(captured), captured)

    if limit == "none":
        notes = enriched.selected_parts[0].doc_notes
        assert notes is not None, "the projected part retains its original note metadata"
        assert notes.projections == captured.doc_notes[0].projections
        assert notes.model_copy(update={"projections": None}) == legacy_notes
    else:
        assert enriched == baseline


def test_doc_physical_projection_rejects_wrong_note_body_hash() -> None:
    data = ole_document(*source_notes())
    captured = extractor().extract_parts_with_doc_notes(data, capture_projection=True)
    payload = captured.model_dump(mode="json")
    payload["doc_notes"][0]["projections"][0]["body"]["text_sha256"] = "a" * 64

    with pytest.raises(ValidationError, match="DOC note body differs"):
        DocNotedParts.model_validate(payload)


@pytest.mark.parametrize("control", ["valid", "unknown", "duplicate", "non_boolean", "custom"])
def test_doc_cfspec_reader_binds_explicit_properties_to_retained_automatic_links(
    control: str,
) -> None:
    source = captured()
    if control == "custom":
        source = source.model_copy(
            update={"links": (source.links[0].model_copy(update={"numbering_flag": 0}),)}
        )
    legacy = DocNoteSummary.from_capture(source, retained_links=1)
    payload = legacy.model_dump(mode="json")
    evidence = {"reference_cp": 1 if control == "unknown" else 0, "value": True, "source": "chpx"}
    if control == "non_boolean":
        evidence["value"] = "true"
    payload["cfspec"] = [evidence, evidence] if control == "duplicate" else [evidence]

    if control == "valid":
        observed = DocNoteSummary.model_validate(payload)
        assert observed.model_dump(mode="json")["cfspec"] == [[0, True, "chpx"]]
        assert (
            observed.model_copy(update={"cfspec": None}).model_dump_json()
            == legacy.model_dump_json()
        )
    else:
        with pytest.raises(ValidationError):
            DocNoteSummary.model_validate(payload)

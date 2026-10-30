from collections.abc import Iterator
from types import MethodType
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest

from lexinform.adapters import document_text
from lexinform.adapters.document_text import DocumentTextExtractor, OdtTextExtractor
from lexinform.models import (
    DocumentPart,
    ExtractedParts,
    OdtNotedParts,
    OdtNoteOrigins,
    OdtPartNoteOrigins,
    OdtTextExtraction,
)
from lexinform.odt_notes import OdtNoteCollector
from tests.unit.test_odt_blocks import odt_document
from tests.unit.test_physical_extraction import archive, extractor

TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"


def note(identity: str = "n1", content: str = "300 zł") -> str:
    return f'<text:note text:id="{identity}" text:note-class="footnote"><text:note-citation>1</text:note-citation><text:note-body><text:p>{content}</text:p></text:note-body></text:note>'


def observed(raw: bytes) -> tuple[OdtTextExtraction, OdtNoteOrigins]:
    legacy, origins = OdtTextExtractor().extract_with_note_origins(raw)
    assert origins is not None, "the unmodified native same-pass collector succeeds"
    return legacy, origins


def test_real_append_offsets_bind_definition_reference_and_exclude_tail() -> None:
    raw = odt_document(
        "<text:p>Before😀"
        + note()
        + 'TAIL<text:note-ref text:ref-name="n1">ref</text:note-ref>AFTER</text:p>'
    )
    legacy, origins = observed(raw)
    assert legacy.legacy_text == OdtTextExtractor().extract(raw) == "Before😀1300 złTAILrefAFTER"
    entry = origins.entries[0]
    assert (
        entry.path == (0, 0) and entry.body_path == (0, 0, 1) and entry.citation_path == (0, 0, 0)
    )
    assert entry.emitted is not None, "the native append cursor witnessed the range"
    assert legacy.blocks[0].text[entry.emitted.start : entry.emitted.end] == "1300 zł"
    assert origins.calls[1].association == "resolved" and origins.calls[1].target_path == entry.path
    assert origins.calls[1].emitted is not None, (
        "reference label appends have their own actual span"
    )
    assert (
        legacy.blocks[0].text[origins.calls[1].emitted.start : origins.calls[1].emitted.end]
        == "ref"
    )
    assert OdtNoteOrigins.model_validate_json(origins.model_dump_json()) == origins


def test_nested_duplicates_and_forward_references_follow_actual_xml_order() -> None:
    raw = odt_document(
        '<text:p><text:note-ref text:ref-name="dup">earlier</text:note-ref>'
        + note("dup", "outer" + note("dup", "inner"))
        + '<text:note-ref text:ref-name="missing">later</text:note-ref></text:p>'
    )
    _, origins = observed(raw)
    assert origins.calls[0].association == "ambiguous"
    assert origins.calls[-1].association == "missing"
    assert origins.entries[1].parent_note_path == origins.entries[0].path
    merged = sorted((*origins.entries, *origins.calls), key=lambda row: row.order)
    assert [row.path for row in merged] == sorted(row.path for row in merged)
    assert [row.order for row in merged] == list(range(len(merged)))


def test_tables_controls_spacing_and_note_tail_preserve_legacy_view() -> None:
    raw = odt_document(
        '<table:table><table:table-row><table:table-cell><text:p>A<text:s text:c="3"/><text:tab/>'
        + note()
        + "<text:line-break/>Z</text:p></table:table-cell><table:table-cell><text:p>B</text:p></table:table-cell></table:table-row></table:table>"
    )
    before = OdtTextExtractor().extract_odt_blocks(raw)
    body, origins = observed(raw)
    assert body == before and body.manifest_sha256 == before.manifest_sha256
    assert origins.entries[0].block_path == (0, 0, 0, 0)
    assert origins.entries[0].emitted is not None, "table paragraph append cursor is observed"
    assert origins.entries[0].emitted.start == 5


def test_physical_nested_leaf_binds_actual_sha_and_existing_options() -> None:
    raw = archive({"nested/source.odt": odt_document("<text:p>" + note() + "</text:p>")})
    reader = extractor()
    before = reader.extract_parts(raw, include_pdf_pages=False)
    result = reader.extract_parts_with_odt_notes(raw, include_pdf_pages=False)
    assert result.legacy.model_dump_json() == before.model_dump_json()
    assert len(result.odt_notes) == 1
    linked = result.odt_notes[0]
    assert linked.part_index == 0
    assert (
        linked.extraction.input_sha256
        == result.legacy.parts[0].sha256
        != result.legacy.input_sha256
    )


@pytest.mark.parametrize("size", [1_000_000, 255])
def test_oversized_identity_closes_prefix_without_losing_later_counts(size: int) -> None:
    raw = odt_document("<text:p>" + note("x" * size) + note("later") + "</text:p>")
    before = OdtTextExtractor().extract(raw)
    legacy, origins = observed(raw)
    assert legacy.legacy_text == before
    assert (
        origins.note_count == origins.call_count == 2 and not origins.entries and not origins.calls
    )
    assert origins.coverage == "partial" and origins.full_manifest_sha256 is None


def test_event_and_byte_prefix_keep_counts_and_never_infer_omitted_targets() -> None:
    raw = odt_document(
        "<text:p>"
        + "".join(note(str(i)) for i in range(80))
        + '<text:note-ref text:ref-name="0">last</text:note-ref></text:p>'
    )
    legacy, origins = observed(raw)
    assert legacy.legacy_text == OdtTextExtractor().extract(raw)
    assert origins.note_count == 80 and origins.call_count == 81
    assert len(origins.entries) + len(origins.calls) <= 64
    assert len(origins.model_dump_json().encode()) <= 32768 and origins.coverage == "partial"


def test_absent_and_empty_ids_are_not_inferred_from_citation() -> None:
    raw = odt_document(
        "<text:p><text:note><text:note-citation>n1</text:note-citation></text:note><text:note-ref>n1</text:note-ref></text:p>"
    )
    _, origins = observed(raw)
    assert origins.entries[0].identity is None
    assert [call.association for call in origins.calls] == ["embedded", "unverified"]


@pytest.mark.parametrize("stage", ["setup", "enter", "leave", "finish"])
def test_optional_hook_failure_preserves_successful_business(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    raw = odt_document("<text:p>" + note() + "</text:p>")
    expected = OdtTextExtractor().extract_odt_blocks(raw)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("CONTROLLED PRIVATE METADATA FAILURE")

    monkeypatch.setattr(
        document_text, "OdtNoteCollector", fail
    ) if stage == "setup" else monkeypatch.setattr(OdtNoteCollector, stage, fail)
    legacy, origins = OdtTextExtractor().extract_with_note_origins(raw)
    assert legacy == expected and origins is None


@pytest.mark.parametrize("stage", ["setup", "enter", "leave", "finish"])
def test_control_flow_cancellation_is_not_fail_open(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    raw = odt_document("<text:p>" + note() + "</text:p>")
    failure = KeyboardInterrupt("cancel")

    def cancel(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(
        document_text, "OdtNoteCollector", cancel
    ) if stage == "setup" else monkeypatch.setattr(OdtNoteCollector, stage, cancel)
    with pytest.raises(KeyboardInterrupt) as caught:
        OdtTextExtractor().extract_with_note_origins(raw)
    assert caught.value is failure


def test_original_parser_exception_and_cause_are_never_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = odt_document("<text:p>" + note() + "</text:p>")
    failure = ValueError("original parser")
    cause = RuntimeError("original cause")

    def parser(*args: object, **kwargs: object) -> Iterator[str]:
        raise failure from cause

    def cleanup(*args: object, **kwargs: object) -> None:
        raise SystemExit("metadata cleanup must not run after failed business")

    monkeypatch.setattr(document_text, "_odt_blocks", parser)
    monkeypatch.setattr(OdtNoteCollector, "finish", cleanup)
    with pytest.raises(ValueError) as caught:
        OdtTextExtractor().extract_with_note_origins(raw)
    assert caught.value is failure and caught.value.__cause__ is cause


def test_plain_api_never_constructs_opt_in_collector(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = odt_document("<text:p>" + note() + "</text:p>")

    def forbidden() -> None:
        raise AssertionError("default off")

    monkeypatch.setattr(document_text, "OdtNoteCollector", forbidden)
    assert OdtTextExtractor().extract(raw) == OdtTextExtractor().extract_odt_blocks(raw).legacy_text


class CustomOdt(OdtTextExtractor):
    def extract_odt_blocks(self, data: bytes) -> OdtTextExtraction:
        return super().extract_odt_blocks(data)


def test_inherited_and_instance_overrides_do_not_claim_native_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = odt_document("<text:p>" + note() + "</text:p>")
    assert CustomOdt().extract_with_note_origins(raw)[1] is None
    reader = OdtTextExtractor()

    def custom(self: OdtTextExtractor, data: bytes) -> str:
        return "custom plain"

    monkeypatch.setattr(reader, "extract", MethodType(custom, reader))
    assert reader.extract_with_note_origins(raw)[1] is None


def test_single_parse_and_content_xml_read() -> None:
    raw = odt_document("<text:p>" + note() + "</text:p>")
    parser = ET.fromstring
    read = document_text._read_xml
    with (
        patch.object(ET, "fromstring", wraps=parser) as parsed,
        patch.object(document_text, "_read_xml", wraps=read) as loaded,
    ):
        observed(raw)
    assert parsed.call_count == loaded.call_count == 1


def test_new_carrier_rejects_untyped_custom_legacy_instead_of_returning_invalid_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = odt_document("<text:p>" + note() + "</text:p>")
    reader = extractor()
    legacy = reader.extract_parts(raw)
    broken = legacy.model_copy(update={"odt_blocks": (legacy.odt_blocks[0].model_dump(),)})

    def custom(data: bytes, *, include_pdf_pages: bool = True) -> ExtractedParts:
        return broken

    def valid_custom(data: bytes, *, include_pdf_pages: bool = True) -> ExtractedParts:
        return legacy

    monkeypatch.setattr(reader, "extract_parts", valid_custom)
    valid = reader.extract_parts_with_odt_notes(raw)
    assert valid.legacy == legacy and OdtNotedParts.model_validate(valid) == valid
    monkeypatch.setattr(reader, "extract_parts", custom)
    with pytest.raises(ValueError):
        reader.extract_parts_with_odt_notes(raw)


def test_optional_carrier_failure_returns_already_validated_empty_carrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = odt_document("<text:p>" + note() + "</text:p>")
    reader = extractor()
    expected = reader.extract_parts(raw)

    def metadata_fault(
        *, legacy: ExtractedParts, odt_notes: tuple[OdtPartNoteOrigins, ...]
    ) -> OdtNotedParts:
        if odt_notes:
            raise RuntimeError("controlled optional validation fault")
        return OdtNotedParts(legacy=legacy, odt_notes=())

    monkeypatch.setattr(document_text, "OdtNotedParts", metadata_fault)
    result = reader.extract_parts_with_odt_notes(raw)
    assert result.legacy == expected and result.odt_notes == ()
    assert OdtNotedParts.model_validate(result) == result


def test_class_level_physical_override_remains_the_actual_business_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = odt_document("<text:p>" + note() + "</text:p>")
    custom = ExtractedParts(
        input_sha256="a" * 64, parts=(DocumentPart(text="custom physical text", sha256="a" * 64),)
    )

    def overridden(
        self: DocumentTextExtractor, data: bytes, *, include_pdf_pages: bool = True
    ) -> ExtractedParts:
        return custom

    monkeypatch.setattr(DocumentTextExtractor, "extract_parts", overridden)
    reader = extractor()
    expected = reader.extract_parts(raw)
    actual = reader.extract_parts_with_odt_notes(raw)
    assert actual.legacy == expected and actual.odt_notes == ()
    assert OdtNotedParts.model_validate(actual) == actual

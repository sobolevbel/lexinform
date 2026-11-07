from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest

from lexinform import extraction_records
from lexinform.adapters import document_text
from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.word_field_ranges import WordFieldRanges
from lexinform.models.extractions import ExtractionResult
from tests.fakes import FakeTextExtractor
from tests.unit.test_doc_note_extraction import source_notes
from tests.unit.test_doc_note_physical_parts import ole_document
from tests.unit.test_physical_extraction import archive
from tests.unit.test_word_note_origins import package


def field_document() -> bytes:
    return package(
        '<w:p><w:ins><w:fldSimple w:instr="DATE">'
        "<w:r><w:t>30 dni</w:t></w:r></w:fldSimple></w:ins></w:p>",
        {},
    )


def test_default_field_prefix_uses_existing_zip_and_xml_walk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = field_document()
    reader = DocumentTextExtractor(FakeTextExtractor(), DocxTextExtractor(), FakeTextExtractor())

    with (
        patch.object(document_text, "_physical_zip", wraps=document_text._physical_zip) as opened,
        patch.object(ET, "fromstring", wraps=ET.fromstring) as parsed,
    ):
        enriched = reader.extract_document_with_provenance(data)
        assert (opened.call_count, parsed.call_count) == (2, 1)

    monkeypatch.setattr(document_text, "_native_word_fields", lambda adapter: False)
    with (
        patch.object(document_text, "_physical_zip", wraps=document_text._physical_zip) as opened,
        patch.object(ET, "fromstring", wraps=ET.fromstring) as parsed,
    ):
        baseline = reader.extract_document_with_provenance(data)
        assert (opened.call_count, parsed.call_count) == (2, 1)

    (part,) = enriched.result.selected_parts
    prefix = part.word_field_prefix
    assert prefix is not None, "Native traversal emitted one field node"
    (paragraph,) = prefix.paragraphs
    (field,) = paragraph.ranges
    assert (field.start, field.end, field.group) == (0, 6, "simple_body_text")
    stripped = enriched.result.model_dump(mode="json")
    stripped["selected_parts"][0].pop("word_field_prefix")
    assert baseline == enriched.model_copy(
        update={"result": ExtractionResult.model_validate(stripped)}
    )


def test_field_prefix_yields_to_prior_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    data = archive(
        {"projekt.doc": ole_document(*source_notes()), "uzasadnienie.docx": field_document()}
    )
    reader = DocumentTextExtractor(FakeTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    enriched = reader.extract_document_with_provenance(data)
    payload = enriched.result.model_dump(mode="json")
    (doc,) = [part for part in payload["selected_parts"] if "doc_notes" in part]
    (word,) = [part for part in payload["selected_parts"] if "word_view_status" in part]
    assert doc["doc_notes"]["projections"]
    assert word.pop("word_field_prefix")
    assert word["word_view_status"]["observed_inline"]
    prior = ExtractionResult.model_validate(payload)
    monkeypatch.setattr(
        extraction_records, "MAX_EXTRACTION_METADATA_BYTES", len(prior.model_dump_json().encode())
    )

    bounded = reader.extract_document_with_provenance(data)

    assert bounded == enriched.model_copy(update={"result": prior})


def test_existing_instance_override_keeps_the_original_hook() -> None:
    data = field_document()
    word = DocxTextExtractor()
    with patch.object(
        word, "extract_with_view_status", wraps=word.extract_with_view_status
    ) as hook:
        reader = DocumentTextExtractor(FakeTextExtractor(), word, FakeTextExtractor())

        observed = reader.extract_document_with_provenance(data)

        hook.assert_called_once_with(data)
    (part,) = observed.result.selected_parts
    assert part.word_view_status is not None
    assert part.word_field_prefix is None


def test_field_prefix_is_capped_in_xml_order() -> None:
    field = '<w:fldSimple w:instr="DATE"><w:r><w:t>30 dni</w:t></w:r></w:fldSimple>'
    data = package("<w:p>" + field * 40 + "</w:p>", {})
    reader = DocumentTextExtractor(FakeTextExtractor(), DocxTextExtractor(), FakeTextExtractor())

    (part,) = reader.extract_document_with_provenance(data).result.selected_parts

    prefix = part.word_field_prefix
    assert prefix is not None, "The native walk observed 40 field nodes"
    (paragraph,) = prefix.paragraphs
    assert len(paragraph.ranges) == 32
    assert tuple(span.path[0] for span in paragraph.ranges) == tuple(range(32))


def test_invalid_field_prefix_preserves_legacy_extraction() -> None:
    reader = DocumentTextExtractor(FakeTextExtractor(), DocxTextExtractor(), FakeTextExtractor())
    data = field_document()
    with patch.object(document_text, "_native_word_fields", return_value=False):
        legacy = reader.extract_document_with_provenance(data)

    with patch.object(WordFieldRanges, "prefix", side_effect=ValueError):
        actual = reader.extract_document_with_provenance(data)

    assert actual == legacy

import hashlib
import zipfile
from typing import IO, Literal
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from tools.replay_word_blocks import snapshot

from lexinform.adapters.document_text import (
    DocumentTextExtractor,
    DocxTextExtractor,
    OdtTextExtractor,
)
from lexinform.document_candidates import select_document_parts
from lexinform.models import WordStructuredParts
from lexinform.ports import WordStructuredPartsExtractor, WordStructureTextExtractor
from tests.fakes import FakeTextExtractor
from tests.unit.test_pdf_physical_parts import PdfWithPages
from tests.unit.test_physical_extraction import archive
from tests.unit.test_word_structure import structured


def extractor(word: DocxTextExtractor | FakeTextExtractor | None = None) -> DocumentTextExtractor:
    return DocumentTextExtractor(
        FakeTextExtractor("PDF text", page_count=1),
        word or DocxTextExtractor(),
        FakeTextExtractor("DOC text"),
    )


def test_native_companion_binds_nested_word_parts_without_changing_legacy_or_selection() -> None:
    word = structured()
    data = archive({"0.pdf": b"%PDF-1.7", "a.docx": word, "nested.zip": archive({"b.docm": word})})
    reader = extractor()
    old = reader.extract_parts(data)
    result = reader.extract_parts_with_word_structure(data)
    assert result.legacy.model_dump_json() == old.model_dump_json()
    assert select_document_parts(result.legacy, max_part_chars=None) == select_document_parts(
        old, max_part_chars=None
    )
    assert [record.part_index for record in result.word_structure] == [1, 2]
    for record in result.word_structure:
        index = record.part_index
        assert result.legacy.parts[index].sha256 == record.extraction.input_sha256
        assert record.extraction.input_sha256 == hashlib.sha256(word).hexdigest()
        assert (
            record.extraction.body_manifest_sha256
            == old.word_blocks[index - 1].extraction.manifest_sha256
        )
        assert (
            record.extraction.notes_manifest_sha256
            == old.word_notes[index - 1].extraction.manifest_sha256
        )
    assert result.word_structure[0].extraction == result.word_structure[1].extraction
    assert WordStructuredParts.model_validate_json(result.model_dump_json()) == result
    assert isinstance(reader, WordStructuredPartsExtractor)
    assert isinstance(DocxTextExtractor(), WordStructureTextExtractor)


def test_native_optin_preserves_exact_zip_read_sequence_and_legacy_facade_is_off() -> None:
    data = archive({"a.docx": structured(), "b.zip": archive({"b.docx": structured()})})
    original = zipfile.ZipFile.open
    reads: list[str] = []

    def opened(
        archive: zipfile.ZipFile,
        name: str | zipfile.ZipInfo,
        mode: Literal["r", "w"] = "r",
        pwd: bytes | None = None,
        *,
        force_zip64: bool = False,
    ) -> IO[bytes]:
        reads.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
        return original(archive, name, mode, pwd, force_zip64=force_zip64)

    reader = extractor()
    with patch.object(zipfile.ZipFile, "open", opened):
        old = reader.extract_parts(data)
    before = tuple(reads)
    reads.clear()
    with patch.object(zipfile.ZipFile, "open", opened):
        result = reader.extract_parts_with_word_structure(data)
    assert tuple(reads) == before and result.legacy == old
    with patch.object(
        DocxTextExtractor, "extract_with_structure", side_effect=AssertionError("optin")
    ):
        assert reader.extract_parts(data) == old
        reader.extract_document(data)
    assert "word_structure" not in old.model_dump()


@pytest.mark.parametrize("custom", [False, True])
def test_unavailable_or_unsupported_structure_stays_unknown_without_second_extraction(
    custom: bool,
) -> None:
    word = FakeTextExtractor("custom Word text") if custom else DocxTextExtractor()
    data = structured()
    with patch.object(word, "extract", wraps=word.extract) as read:
        result = extractor(word).extract_parts_with_word_structure(data)
    if custom:
        assert not result.word_structure and read.call_count == 1
        assert result.legacy.parts[0].text == "custom Word text"
    else:
        assert len(result.word_structure) == 1 and read.call_count == 0


@pytest.mark.parametrize("failure", [ValueError("actual parser"), KeyboardInterrupt("actual stop")])
def test_optin_failure_preserves_existing_rejection_or_original_exception_without_retry(
    failure: BaseException,
) -> None:
    data = structured()
    if isinstance(failure, ValueError):
        with patch.object(DocxTextExtractor, "extract_with_notes", side_effect=failure):
            old = extractor().extract_parts(data)
        with patch.object(DocxTextExtractor, "extract_with_structure", side_effect=failure) as read:
            result = extractor().extract_parts_with_word_structure(data)
        assert result.legacy == old and not result.word_structure and read.call_count == 1
        assert old.rejected and not old.parts
    else:
        with (
            patch.object(DocxTextExtractor, "extract_with_structure", side_effect=failure) as read,
            pytest.raises(type(failure)) as caught,
        ):
            extractor().extract_parts_with_word_structure(data)
        assert caught.value is failure and read.call_count == 1


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_body",
        "missing_notes",
        "duplicate",
        "index",
        "input",
        "body",
        "notes",
        "xml",
        "text",
        "bool_index",
    ],
)
def test_companion_reader_rejects_corrupt_or_missing_actual_bindings(corruption: str) -> None:
    result = extractor().extract_parts_with_word_structure(structured())
    payload = result.model_dump(mode="json")
    link = payload["word_structure"][0]
    if corruption == "missing_body":
        payload["legacy"]["word_blocks"] = []
    elif corruption == "missing_notes":
        payload["legacy"]["word_notes"] = []
    elif corruption == "duplicate":
        payload["word_structure"].append(link)
    elif corruption == "index":
        link["part_index"] = 1
    elif corruption == "bool_index":
        link["part_index"] = False
    elif corruption == "text":
        payload["legacy"]["parts"][0]["text"] = "different actual body"
    else:
        field = {
            "input": "input_sha256",
            "body": "body_manifest_sha256",
            "notes": "notes_manifest_sha256",
            "xml": "xml_sha256",
        }[corruption]
        link["extraction"][field] = "a" * 64
    with pytest.raises(ValidationError):
        WordStructuredParts.model_validate(payload)


def test_companion_rejects_out_of_input_order_links_and_revalidates_constructed_legacy() -> None:
    result = extractor().extract_parts_with_word_structure(
        archive({"a.docx": structured(), "b.docx": structured()})
    )
    with pytest.raises(ValidationError):
        WordStructuredParts(
            legacy=result.legacy, word_structure=tuple(reversed(result.word_structure))
        )
    corrupt = result.legacy.model_copy(update={"word_blocks": ()})
    with pytest.raises(ValidationError):
        WordStructuredParts(legacy=corrupt, word_structure=result.word_structure)


def test_nonword_native_manifests_remain_available_without_fake_word_coordinates() -> None:
    odt = archive(
        {
            "mimetype": b"application/vnd.oasis.opendocument.text",
            "content.xml": b'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text><text:p>ODT text</text:p></office:text></office:body></office:document-content>',
        }
    )
    data = archive({"a.pdf": b"%PDF-1.7", "b.odt": odt})
    reader = extractor()
    result = reader.extract_parts_with_word_structure(data)
    assert result.legacy == reader.extract_parts(data) and not result.word_structure
    assert len(result.legacy.odt_blocks) == 1
    assert result.legacy.odt_blocks[0].extraction.legacy_text == OdtTextExtractor().extract(odt)


def test_existing_replay_consumer_uses_native_companion_and_same_independent_verifier() -> None:
    data = structured()
    original = DocumentTextExtractor.extract_parts_with_word_structure
    calls: list[bytes] = []

    def captured(
        reader: DocumentTextExtractor, value: bytes, *, include_pdf_pages: bool = True
    ) -> WordStructuredParts:
        calls.append(value)
        return original(reader, value, include_pdf_pages=include_pdf_pages)

    old_core, old_meta = snapshot(data, adapter=True, physical_parts=True, with_notes=True)
    with patch.object(DocumentTextExtractor, "extract_parts_with_word_structure", captured):
        core, meta = snapshot(
            data, adapter=True, physical_parts=True, with_notes=True, with_structure=True
        )
    assert core == old_core and calls == [data]
    assert meta is not None, "The actual structured fixture retains physical manifests"
    assert {key: value for key, value in meta.items() if key != "structure"} == old_meta
    assert meta["structure"]["independent_xml_paths"] is True
    direct_core, direct_meta = snapshot(data, adapter=True, with_notes=True, with_structure=True)
    assert direct_core == core and direct_meta is not None
    assert direct_meta["structure"] == meta["structure"]


@pytest.mark.parametrize("include_pdf_pages", [None, False, True])
def test_companion_preserves_explicit_and_default_mixed_package_metadata_options(
    include_pdf_pages: bool | None,
) -> None:
    pdf = PdfWithPages()
    reader = DocumentTextExtractor(pdf, DocxTextExtractor(), FakeTextExtractor("DOC text"))
    data = archive({"a.pdf": b"%PDF-1.7", "b.docx": structured()})
    options = {} if include_pdf_pages is None else {"include_pdf_pages": include_pdf_pages}
    result = reader.extract_parts_with_word_structure(data, **options)
    assert pdf.page_reads == int(include_pdf_pages is not False)
    old = reader.extract_parts(data, **options)
    assert result.legacy.model_dump_json() == old.model_dump_json()
    assert bool(result.legacy.pdf_pages) == (include_pdf_pages is not False)
    assert [value.part_index for value in result.word_structure] == [1]

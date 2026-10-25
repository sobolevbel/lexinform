import hashlib

import pytest
from pydantic import ValidationError

from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.models import (
    DocumentPart,
    ExtractedParts,
    WordDetailedExtraction,
    WordPartExtraction,
    WordTextExtraction,
)
from tests.fakes import FakeTextExtractor
from tests.unit.test_physical_extraction import archive, docx


class CountingWord(DocxTextExtractor):
    def __init__(self) -> None:
        self.block_reads = 0
        self.note_reads = 0
        self.string_reads = 0

    def extract_with_notes(self, data: bytes) -> WordDetailedExtraction:
        self.note_reads += 1
        return super().extract_with_notes(data)

    def extract(self, data: bytes) -> str:
        self.string_reads += 1
        return super().extract(data)

    def extract_blocks(self, data: bytes) -> WordTextExtraction:
        self.block_reads += 1
        return super().extract_blocks(data)


def extractor(reader: DocxTextExtractor | FakeTextExtractor) -> DocumentTextExtractor:
    return DocumentTextExtractor(FakeTextExtractor("PDF"), reader, FakeTextExtractor("DOC"))


def test_word_blocks_refer_to_root_physical_bytes_and_exact_part_text() -> None:
    data = docx("Art. 1. Termin 30 dni.")
    word = CountingWord()

    result = extractor(word).extract_parts(data)

    assert len(result.parts) == len(result.word_blocks) == 1
    linked = result.word_blocks[0]
    assert linked.part_index == 0
    assert linked.extraction.input_sha256 == result.parts[0].sha256 == result.input_sha256
    assert result.parts[0].text == linked.extraction.legacy_text == "Art. 1. Termin 30 dni."
    assert result.parts[0].role == "unknown" and result.parts[0].pages == 0
    assert word.block_reads + word.note_reads == 1 and word.string_reads == 0
    assert ExtractedParts.model_validate_json(result.model_dump_json()) == result


def test_identical_word_bytes_in_nested_members_keep_distinct_physical_links() -> None:
    data = docx("USTAWA")
    bundle = archive({"a.docx": data, "nested.zip": archive({"b.docx": data})})

    result = extractor(DocxTextExtractor()).extract_parts(bundle)

    assert [part.members for part in result.parts] == [("a.docx",), ("nested.zip", "b.docx")]
    assert [linked.part_index for linked in result.word_blocks] == [0, 1]
    assert result.word_blocks[0].extraction == result.word_blocks[1].extraction
    assert all(part.container_sha256 == hashlib.sha256(bundle).hexdigest() for part in result.parts)
    assert all(part.sha256 == hashlib.sha256(data).hexdigest() for part in result.parts)


def test_normal_legal_selection_and_explicit_opt_out_do_not_retain_word_blocks() -> None:
    data = docx("USTAWA")
    word = CountingWord()
    reader = extractor(word)

    physical = reader.extract_parts(data, include_word_blocks=False)
    selected = reader.extract_document(data)

    assert not physical.word_blocks
    assert physical.parts[0].text == selected.text == "USTAWA"
    assert word.block_reads == word.note_reads == 0 and word.string_reads == 2


def test_a_legacy_reader_does_not_fabricate_word_block_records() -> None:
    result = extractor(FakeTextExtractor("legacy text")).extract_parts(docx("USTAWA"))

    assert result.parts[0].text == "legacy text" and not result.word_blocks


def test_an_unsupported_word_body_keeps_its_identity_without_claiming_blocks() -> None:
    data = archive({"word/document.xml": b"<other/>"})

    result = extractor(DocxTextExtractor()).extract_parts(data)

    assert result.parts[0].losses == ("no extracted text",)
    assert len(result.word_blocks) == 1
    manifest = result.word_blocks[0].extraction
    assert manifest.body_state == "unsupported" and not manifest.blocks


def test_a_malformed_word_member_keeps_the_rejection_without_a_manifest() -> None:
    data = archive({"word/document.xml": b"<broken"})

    result = extractor(DocxTextExtractor()).extract_parts(data)

    assert not result.parts and not result.word_blocks
    assert result.rejected == ("(): ParseError",)


@pytest.mark.parametrize("problem", ["missing", "duplicate", "different_bytes", "different_text"])
def test_word_records_reject_an_invalid_physical_binding(problem: str) -> None:
    manifest = DocxTextExtractor().extract_blocks(docx("body"))
    part = DocumentPart(sha256=manifest.input_sha256, text="body")
    links: tuple[WordPartExtraction, ...] = (WordPartExtraction(part_index=0, extraction=manifest),)
    if problem == "missing":
        links = (WordPartExtraction(part_index=1, extraction=manifest),)
    elif problem == "duplicate":
        links *= 2
    elif problem == "different_bytes":
        part = part.model_copy(update={"sha256": "f" * 64})
    else:
        part = part.model_copy(update={"text": "other"})

    with pytest.raises(ValidationError, match="Word blocks"):
        ExtractedParts(input_sha256="0" * 64, parts=(part,), word_blocks=links)

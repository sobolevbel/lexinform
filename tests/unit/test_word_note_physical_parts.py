import hashlib

import pytest
from pydantic import ValidationError

from lexinform.adapters.document_text import DocxTextExtractor
from lexinform.models import ExtractedParts, WordPartNoteExtraction, WordTextExtraction
from tests.fakes import FakeTextExtractor
from tests.unit.test_physical_extraction import archive
from tests.unit.test_word_note_origins import call, entry, note_xml, package
from tests.unit.test_word_physical_parts import CountingWord, extractor


def document() -> bytes:
    return package(
        "<w:p>" + call("2") + "</w:p>",
        {"word/footnotes.xml": note_xml("footnote", entry("2", "30 dni"))},
    )


def test_note_manifest_binds_to_its_single_body_and_physical_bytes() -> None:
    data = document()
    word = CountingWord()

    result = extractor(word).extract_parts(data)

    assert len(result.word_notes) == len(result.word_blocks) == len(result.parts) == 1
    linked = result.word_notes[0]
    assert linked.part_index == 0
    assert (
        linked.extraction.input_sha256 == result.parts[0].sha256 == hashlib.sha256(data).hexdigest()
    )
    assert (
        linked.extraction.body_manifest_sha256 == result.word_blocks[0].extraction.manifest_sha256
    )
    assert linked.extraction.calls[0].target == 0
    assert word.note_reads == 1 and word.block_reads == word.string_reads == 0
    assert ExtractedParts.model_validate_json(result.model_dump_json()) == result
    payload = result.model_dump(mode="json")["word_notes"][0]["extraction"]
    assert "body" not in payload and payload["body_manifest_sha256"]


def test_duplicate_files_inside_nested_zips_keep_separate_note_links() -> None:
    data = document()
    bundle = archive({"a.docx": data, "nested.zip": archive({"b.docx": data})})

    result = extractor(DocxTextExtractor()).extract_parts(bundle)

    assert [part.members for part in result.parts] == [("a.docx",), ("nested.zip", "b.docx")]
    assert [notes.part_index for notes in result.word_notes] == [0, 1]
    assert result.word_notes[0].extraction == result.word_notes[1].extraction
    assert all(part.container_sha256 == hashlib.sha256(bundle).hexdigest() for part in result.parts)


def test_notes_can_be_disabled_while_retaining_body_blocks() -> None:
    word = CountingWord()

    result = extractor(word).extract_parts(document(), include_word_notes=False)

    assert len(result.word_blocks) == 1 and not result.word_notes
    assert word.note_reads == 0 and word.block_reads == 1 and word.string_reads == 0


def test_disabling_body_blocks_also_disables_dependent_note_manifests() -> None:
    word = CountingWord()

    result = extractor(word).extract_parts(document(), include_word_blocks=False)

    assert not result.word_blocks and not result.word_notes
    assert word.note_reads == word.block_reads == 0 and word.string_reads == 1


class BodyOnlyWord(FakeTextExtractor):
    def extract_blocks(self, data: bytes) -> WordTextExtraction:
        return DocxTextExtractor().extract_blocks(data)


def test_body_only_readers_do_not_fabricate_note_manifests() -> None:
    result = extractor(BodyOnlyWord()).extract_parts(document())

    assert len(result.word_blocks) == 1 and not result.word_notes


@pytest.mark.parametrize("problem", ["orphan", "duplicate", "missing_index", "bytes", "body"])
def test_incorrect_note_to_body_links_are_rejected(problem: str) -> None:
    result = extractor(DocxTextExtractor()).extract_parts(document())
    notes = result.word_notes[0].extraction
    linked = result.word_notes[0]
    bodies = result.word_blocks
    if problem == "orphan":
        bodies = ()
    elif problem == "missing_index":
        linked = WordPartNoteExtraction(part_index=100, extraction=notes)
    elif problem == "bytes":
        linked = WordPartNoteExtraction(
            part_index=0, extraction=notes.model_copy(update={"input_sha256": "f" * 64})
        )
    elif problem == "body":
        linked = WordPartNoteExtraction(
            part_index=0, extraction=notes.model_copy(update={"body_manifest_sha256": "f" * 64})
        )
    links = (linked, linked) if problem == "duplicate" else (linked,)

    with pytest.raises(ValidationError, match="Word notes"):
        ExtractedParts(
            input_sha256=result.input_sha256,
            parts=result.parts,
            word_blocks=bodies,
            word_notes=links,
        )

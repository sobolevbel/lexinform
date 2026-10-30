import hashlib
import io
import json
import zipfile
from typing import IO, Literal
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest

from lexinform.adapters.document_text import DocxTextExtractor
from lexinform.models import (
    WordStructureBlock,
    WordStructureCall,
    WordStructuredExtraction,
    WordStructureSupport,
)
from tests.unit.test_physical_extraction import archive as package_files
from tests.unit.test_word_blocks import NS, paragraph
from tests.unit.test_word_note_origins import call, entry, note_xml, package


def structured() -> bytes:
    return package(
        "<w:p>"
        + call("2")
        + "</w:p><w:unknown><w:p><w:r><w:t>NOT READ</w:t></w:r></w:p></w:unknown>",
        {
            "word/footnotes.xml": note_xml(
                "footnote",
                '<w:footnote w:id="2"><w:tbl><w:tr><w:tc>'
                + paragraph("30 dni")
                + "<w:p>"
                + call("7", kind="endnote")
                + "</w:p></w:tc></w:tr></w:tbl></w:footnote>",
            ),
            "word/endnotes.xml": note_xml("endnote", entry("7", "500 zł", kind="endnote")),
        },
    )


def verify_paths(data: bytes, result: WordStructuredExtraction) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        roots = {name: ET.fromstring(archive.read(name)) for name in archive.namelist()}
    for record in (*result.structure.blocks, *result.structure.calls, *result.structure.support):
        node = roots[record.xml_part]
        for index in record.path:
            node = node[index]
        if isinstance(record, WordStructureBlock):
            assert node.tag.rsplit("}", 1)[-1] in ("p", "tr", "tc")
        if isinstance(record, WordStructureSupport):
            assert node.tag == record.tag and len(node) == record.child_count
        if isinstance(record, WordStructureCall):
            assert node.tag.rsplit("}", 1)[-1] == record.call.kind + "Reference"


def test_note_structure_and_nested_calls_use_actual_parts_without_changing_legacy() -> None:
    data = structured()
    reader = DocxTextExtractor()
    old = reader.extract_with_notes(data)
    result = reader.extract_with_structure(data)
    assert result.legacy.model_dump_json() == old.model_dump_json()
    assert result.structure.coverage == "complete"
    note_blocks = [b for b in result.structure.blocks if b.xml_part == "word/footnotes.xml"]
    assert {b.kind for b in note_blocks} == {"paragraph", "table_row", "table_cell"}
    assert all(b.note_id == "word-note:word/footnotes.xml:0" for b in note_blocks)
    nested = next(c for c in result.structure.calls if c.xml_part == "word/footnotes.xml")
    assert (
        nested.call.target == 1
        and nested.call.state == "expanded"
        and nested.legacy_view == "missing_marker"
    )
    assert "[nieodczytany przypis 7]" in result.legacy.notes.notes[0].text
    assert any(s.view == "skipped_block" and s.child_count == 1 for s in result.structure.support)
    assert len(result.legacy.notes.calls) == 1
    verify_paths(data, result)
    assert WordStructuredExtraction.model_validate_json(result.model_dump_json()) == result


def test_optin_uses_exact_same_zip_reads_and_legacy_methods_remain_off() -> None:
    data = structured()
    reader = DocxTextExtractor()
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

    with patch.object(zipfile.ZipFile, "open", opened):
        reader.extract_with_notes(data)
    before = tuple(reads)
    reads.clear()
    with patch.object(zipfile.ZipFile, "open", opened):
        reader.extract_with_structure(data)
    assert (
        tuple(reads) == before == ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml")
    )
    assert "word-structure" not in reader.extract_with_notes(data).model_dump_json()


def test_duplicate_and_missing_note_targets_keep_last_metadata_target_and_actual_view() -> None:
    data = package(
        "<w:p>" + call("2") + call("99") + "</w:p>",
        {"word/footnotes.xml": note_xml("footnote", entry("2", "first") + entry("2", "last"))},
    )
    result = DocxTextExtractor().extract_with_structure(data)
    assert [c.call.target for c in result.structure.calls] == [1, None]
    assert [c.call.state for c in result.structure.calls] == ["expanded", "missing"]
    assert result.legacy == DocxTextExtractor().extract_with_notes(data)


@pytest.mark.parametrize("strict", [False, True])
def test_fallback_fields_changes_and_inline_notes_keep_the_exact_legacy_view(strict: bool) -> None:
    body = (
        "<w:p><mc:AlternateContent><mc:Choice>"
        + paragraph("CHOICE")
        + "</mc:Choice><mc:Fallback>"
        + paragraph("FALLBACK")
        + '</mc:Fallback></mc:AlternateContent><w:fldSimple w:instr="not a text"><w:r><w:t>FIELD</w:t></w:r></w:fldSimple><w:ins><w:r><w:t>INSERT</w:t></w:r></w:ins><w:del><w:r><w:delText>DELETED</w:delText></w:r></w:del><w:footnote>'
        + paragraph("INLINE")
        + "</w:footnote></w:p>"
    )
    data = package(body, {})
    if strict:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
        data = package_files(
            {
                "word/document.xml": xml.replace(
                    NS.encode(), b"http://purl.oclc.org/ooxml/wordprocessingml/main"
                )
            }
        )
    reader = DocxTextExtractor()
    old = reader.extract_with_notes(data)
    result = reader.extract_with_structure(data)
    assert (
        result.legacy == old
        and "FALLBACK" not in old.body.legacy_text
        and "DELETED" not in old.body.legacy_text
    )
    assert {"ignored_fallback", "recursed", "ignored_leaf", "inline_note"} <= {
        s.view for s in result.structure.support
    }
    assert len(result.structure.blocks) > len(old.body.blocks)
    verify_paths(data, result)


@pytest.mark.parametrize("max_events,max_bytes", [(1, 524288), (4096, 4096), (4, 6000)])
def test_bounded_prefix_retains_actual_counts_and_unknown_full_hash(
    max_events: int, max_bytes: int
) -> None:
    reader = DocxTextExtractor()
    full = reader.extract_with_structure(structured())
    limited = reader.extract_with_structure(
        structured(), max_events=max_events, max_bytes=max_bytes
    )
    assert limited.legacy == full.legacy
    assert limited.structure.limit_reached and limited.structure.coverage == "partial"
    assert limited.structure.observed_stream_sha256 is None
    assert (
        limited.structure.block_count,
        limited.structure.call_count,
        limited.structure.support_count,
    ) == (full.structure.block_count, full.structure.call_count, full.structure.support_count)
    assert (
        len(
            json.dumps(
                limited.structure.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":")
            ).encode()
        )
        <= max_bytes
    )


@pytest.mark.parametrize(
    "payload",
    [
        b'<w:footnotes xmlns:w="' + NS.encode() + b'"/>',
        b'<!DOCTYPE x [<!ENTITY x "no">]><w:footnotes xmlns:w="' + NS.encode() + b'"/>',
        b"<alien/>",
    ],
)
def test_empty_refused_and_unsupported_note_xml_do_not_fabricate_complete_support(
    payload: bytes,
) -> None:
    data = package("<w:p>" + call("9") + "</w:p>", {"word/footnotes.xml": payload})
    reader = DocxTextExtractor()
    result = reader.extract_with_structure(data)
    assert result.legacy == reader.extract_with_notes(data)
    if result.legacy.notes.parts[0].state != "read":
        assert (
            result.structure.coverage == "partial"
            and result.structure.observed_stream_sha256 is None
        )
    verify_paths(data, result)


@pytest.mark.parametrize(
    "before,after",
    [
        ("30 dni", "60 dni"),
        ("500 zł", "700 zł"),
        ("nie stosuje", "stosuje"),
        ("cudzoziemiec", "każda osoba"),
        ("art. 7", "art. 8"),
        ("2026-10-30", "2027-01-01"),
    ],
)
def test_meaningful_note_changes_survive_legacy_and_structural_fingerprints(
    before: str, after: str
) -> None:
    reader = DocxTextExtractor()
    results = [
        reader.extract_with_structure(
            package(
                "<w:p>" + call("2") + "</w:p>",
                {"word/footnotes.xml": note_xml("footnote", entry("2", value))},
            )
        )
        for value in (before, after)
    ]
    assert results[0].legacy.body.legacy_text != results[1].legacy.body.legacy_text
    assert results[0].structure.manifest_sha256 != results[1].structure.manifest_sha256
    for value, result in zip((before, after), results, strict=True):
        block = next(b for b in result.structure.blocks if b.xml_part == "word/footnotes.xml")
        assert block.text_sha256 == hashlib.sha256(value.encode()).hexdigest()

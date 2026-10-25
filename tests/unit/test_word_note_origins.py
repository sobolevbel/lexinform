import hashlib
from typing import Literal
from xml.etree import ElementTree as ET

import pytest
from pydantic import ValidationError

from lexinform.adapters.document_text import DocxTextExtractor
from lexinform.models import WordDetailedExtraction, WordNoteCall, WordNoteExtraction
from tests.unit.test_physical_extraction import archive
from tests.unit.test_word_blocks import NS, paragraph


def package(body: str, parts: dict[str, bytes]) -> bytes:
    return archive(
        {
            "word/document.xml": (
                f'<w:document xmlns:w="{NS}" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><w:body>{body}</w:body></w:document>'
            ).encode(),
            **parts,
        }
    )


def note_xml(kind: Literal["footnote", "endnote"], body: str) -> bytes:
    return f'<w:{kind}s xmlns:w="{NS}">{body}</w:{kind}s>'.encode()


def entry(identity: str, text: str, *, kind: str = "footnote") -> str:
    return f'<w:{kind} w:id="{identity}">{paragraph(text)}</w:{kind}>'


def call(identity: str, *, kind: str = "footnote") -> str:
    return f'<w:r><w:{kind}Reference w:id="{identity}"/></w:r>'


def extracted() -> WordDetailedExtraction:
    return DocxTextExtractor().extract_with_notes(
        package(
            "<w:p>" + call("2") + "</w:p>",
            {"word/footnotes.xml": note_xml("footnote", entry("2", "30 dni"))},
        )
    )


def test_note_calls_and_targets_resolve_in_the_original_xml_before_flattening() -> None:
    body = (
        "<w:p><w:hyperlink>"
        + call("2")
        + "</w:hyperlink></w:p>"
        + "<w:tbl><w:tr><w:tc><w:p>"
        + call("7", kind="endnote")
        + "</w:p></w:tc></w:tr></w:tbl>"
    )
    parts = {
        "word/footnotes.xml": note_xml("footnote", entry("2", "Zażółć 30 dni")),
        "word/endnotes.xml": note_xml("endnote", entry("7", "500 zł", kind="endnote")),
    }
    data = package(body, parts)
    reader = DocxTextExtractor()

    result = reader.extract_with_notes(data)

    assert result.body == reader.extract_blocks(data)
    assert (
        result.body.legacy_text
        == reader.extract(data)
        == " [przypis 2: Zażółć 30 dni] \n [przypis 7: 500 zł] "
    )
    assert [item.path for item in result.notes.calls] == [(0, 0, 0, 0, 0), (0, 1, 0, 0, 0, 0, 0)]
    root = ET.fromstring(f'<w:document xmlns:w="{NS}"><w:body>{body}</w:body></w:document>')
    for item in result.notes.calls:
        node = root
        for index in item.path:
            node = node[index]
        assert node.tag == f"{{{NS}}}{item.kind}Reference"
        assert node.get(f"{{{NS}}}id") == item.identity
        assert item.target is not None, "supported fixtures resolve every body note call"
        target = result.notes.notes[item.target]
        source = ET.fromstring(parts[target.xml_part])
        for index in target.path:
            source = source[index]
        assert source.tag == f"{{{NS}}}{item.kind}"
        assert source.get(f"{{{NS}}}id") == target.identity
    assert result.notes.parts[0].sha256 == hashlib.sha256(parts["word/footnotes.xml"]).hexdigest()
    assert WordDetailedExtraction.model_validate_json(result.model_dump_json()) == result


def test_missing_empty_and_whitespace_notes_keep_the_exact_legacy_distinction() -> None:
    data = package(
        "<w:p>" + call("1") + call("2") + call("3") + "</w:p>",
        {"word/footnotes.xml": note_xml("footnote", entry("1", "") + entry("2", " "))},
    )

    result = DocxTextExtractor().extract_with_notes(data)

    assert [item.state for item in result.notes.calls] == ["empty", "expanded", "missing"]
    assert [item.target for item in result.notes.calls] == [0, 1, None]
    assert (
        result.body.legacy_text
        == " [nieodczytany przypis 1]  [przypis 2:  ]  [nieodczytany przypis 3] "
    )


def test_duplicate_note_ids_retain_both_origins_and_the_legacy_last_target() -> None:
    data = package(
        "<w:p>" + call("2") + "</w:p>",
        {"word/footnotes.xml": note_xml("footnote", entry("2", "old") + entry("2", "new"))},
    )

    result = DocxTextExtractor().extract_with_notes(data)

    assert [note.path for note in result.notes.notes] == [(0,), (1,)]
    assert result.notes.calls[0].target == 1
    assert result.body.legacy_text == " [przypis 2: new] "
    assert result.notes.losses == ("duplicate note ID: footnote/2",)
    bad = result.notes.model_dump(mode="json")
    bad["calls"][0]["target"] = 0
    with pytest.raises(ValidationError, match="different source"):
        WordNoteExtraction.model_validate(bad)


@pytest.mark.parametrize("nonstandard", ["root", "entry", "duplicate_entry"])
def test_nonstandard_note_xml_keeps_legacy_text_with_unverified_origin(nonstandard: str) -> None:
    notes = note_xml("footnote", entry("2", "legacy", kind="other"))
    if nonstandard == "root":
        notes = notes.replace(b"w:footnotes", b"w:other")
    elif nonstandard == "duplicate_entry":
        notes = note_xml("footnote", entry("2", "first") + entry("2", "legacy", kind="other"))
    data = package("<w:p>" + call("2") + "</w:p>", {"word/footnotes.xml": notes})

    result = DocxTextExtractor().extract_with_notes(data)

    assert result.body.legacy_text == DocxTextExtractor().extract(data) == " [przypis 2: legacy] "
    assert result.notes.calls[0].state == "unverified" and result.notes.calls[0].target is None


def test_a_note_without_an_id_is_not_silently_read_in_a_new_view() -> None:
    notes = note_xml("footnote", "<w:footnote>" + paragraph("not addressed") + "</w:footnote>")
    result = DocxTextExtractor().extract_with_notes(
        package("<w:p>" + call("2") + "</w:p>", {"word/footnotes.xml": notes})
    )

    assert not result.notes.notes and result.notes.calls[0].state == "missing"
    assert "note entry without an ID was not read" in result.notes.losses


def test_refused_xml_records_the_part_without_claiming_a_note_or_body_read() -> None:
    notes = b'<!DOCTYPE root [<!ENTITY x "bad">]><root/>'
    data = package("<w:p>" + call("2") + "</w:p>", {"word/footnotes.xml": notes})

    result = DocxTextExtractor().extract_with_notes(data)

    assert result.body.legacy_text == DocxTextExtractor().extract(data) == ""
    assert result.body.body_state == "unsupported"
    assert result.notes.parts[0].state == "refused"
    assert not result.notes.calls and not result.notes.notes


def test_fallback_notes_are_not_duplicate_calls_and_inline_notes_stay_untraced() -> None:
    body = (
        "<w:p>"
        + call("2")
        + "<mc:Fallback>"
        + call("3")
        + "</mc:Fallback><w:footnote>"
        + paragraph("inline")
        + "</w:footnote></w:p>"
    )
    data = package(body, {"word/footnotes.xml": note_xml("footnote", entry("2", "real"))})

    result = DocxTextExtractor().extract_with_notes(data)

    assert len(result.notes.calls) == 1 and result.notes.calls[0].identity == "2"
    assert "inline Word notes lack note-part origins" in result.notes.losses
    assert result.body.legacy_text == DocxTextExtractor().extract(data)


@pytest.mark.parametrize("field", ["input_sha256", "body_manifest_sha256"])
def test_notes_cannot_bind_to_a_different_input_or_body(field: str) -> None:
    result = extracted()
    notes = result.notes.model_copy(update={field: "f" * 64})

    with pytest.raises(ValidationError, match="different body"):
        WordDetailedExtraction(body=result.body, notes=notes)


@pytest.mark.parametrize(
    "problem", ["target", "identity", "kind", "state", "duplicate", "paragraph", "negative"]
)
def test_corrupt_note_links_or_xml_coordinates_are_rejected(problem: str) -> None:
    data = extracted().model_dump(mode="json")
    item = data["notes"]["calls"][0]
    if problem == "target":
        item["target"] = 100
    elif problem == "identity":
        item["identity"] = "different"
    elif problem == "kind":
        item["kind"] = "endnote"
    elif problem == "state":
        item["state"] = "missing"
    elif problem == "duplicate":
        data["notes"]["calls"].append(dict(item))
    elif problem == "paragraph":
        item["paragraph_path"], item["path"] = [0, 100], [0, 100, 0]
    else:
        item["path"] = [-1]

    with pytest.raises(ValidationError, match="Word"):
        WordDetailedExtraction.model_validate(data)


def test_note_calls_require_an_actual_descendant_position() -> None:
    with pytest.raises(ValidationError, match="inside its paragraph"):
        WordNoteCall(
            path=(0, 1), paragraph_path=(0, 1), kind="footnote", identity="2", state="missing"
        )


def test_strict_word_namespaces_keep_the_same_xml_origins() -> None:
    strict = "http://purl.oclc.org/ooxml/wordprocessingml/main"
    body = "<w:p>" + call("2") + "</w:p>"
    data = archive(
        {
            "word/document.xml": f'<w:document xmlns:w="{strict}"><w:body>{body}</w:body></w:document>'.encode(),
            "word/footnotes.xml": note_xml("footnote", entry("2", "strict")).replace(
                NS.encode(), strict.encode()
            ),
        }
    )

    result = DocxTextExtractor().extract_with_notes(data)

    assert result.notes.parts[0].state == "read"
    assert result.notes.calls[0].target == 0 and result.notes.calls[0].state == "expanded"
    assert result.body.legacy_text == DocxTextExtractor().extract(data)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("30 dni", "60 dni"),
        ("500 zł", "600 zł"),
        ("nie dotyczy", "dotyczy"),
        ("cudzoziemców", "obywateli polskich"),
        ("art. 1", "art. 2"),
        ("1 stycznia 2026 r.", "2 stycznia 2026 r."),
    ],
)
def test_meaningful_note_changes_keep_distinct_text_and_source_identity(
    before: str, after: str
) -> None:
    body = "<w:p>" + call("2") + "</w:p>"
    reader = DocxTextExtractor()
    old = reader.extract_with_notes(
        package(body, {"word/footnotes.xml": note_xml("footnote", entry("2", before))})
    )
    new_data = package(body, {"word/footnotes.xml": note_xml("footnote", entry("2", after))})

    new = reader.extract_with_notes(new_data)

    assert old.body.xml_sha256 == new.body.xml_sha256
    assert old.body.legacy_text != new.body.legacy_text == reader.extract(new_data)
    assert old.notes.notes[0].text_sha256 != new.notes.notes[0].text_sha256
    assert old.notes.manifest_sha256 != new.notes.manifest_sha256

import json
from pathlib import Path

import pytest
from tools.corpus_artifacts import digest
from tools.replay_word_textboxes import run, snapshot

from tests.unit.test_physical_extraction import archive
from tests.unit.test_word_blocks import NS, paragraph, word


def test_word_textbox_replay_separates_raw_branches_from_actual_body_walk(tmp_path: Path) -> None:
    ordinary = "<w:txbxContent>" + paragraph("ordinary contents") + "</w:txbxContent>"
    choice = "<w:txbxContent>" + paragraph("choice contents") + "</w:txbxContent>"
    fallback = "<w:txbxContent>" + paragraph("fallback contents") + "</w:txbxContent>"
    data = word(
        "<w:p><w:r>" + ordinary + "<mc:AlternateContent>"
        "<mc:Choice>" + choice + "</mc:Choice><mc:Fallback>" + fallback + "</mc:Fallback>"
        "</mc:AlternateContent></w:r></w:p>"
    )
    corpus = tmp_path / "corpus"
    files = corpus / "documents/files"
    files.mkdir(parents=True)
    source = files / "sample.docx"
    source.write_bytes(data)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "status": "recorded",
                "suite": "external",
                "observations": {"word/sample.docx": {"actual": {"sha256": digest(data)}}},
            }
        )
    )
    output = tmp_path / "report.json"

    run(corpus, catalog, digest(catalog.read_bytes()), (source.name,), output)

    text = output.read_text()
    report = json.loads(text)
    row = report["observations"]["documents/files/sample.docx"]
    assert row["raw_textboxes"] == {"ordinary": 1, "choice": 1, "fallback": 1}
    assert row["visited_textboxes"] == {"recursed": 2}
    assert row["legacy_text_sha256"] == digest(b"ordinary contentschoice contents")
    assert "contents" not in text and report["completeness"] == "not_asserted"

    source.write_bytes(data + b"changed")
    refused = tmp_path / "refused.json"
    with pytest.raises(ValueError, match="Raw Word input SHA mismatch"):
        run(corpus, catalog, digest(catalog.read_bytes()), (source.name,), refused)
    assert not refused.exists()


def test_word_textbox_replay_records_zero_body_boxes_and_ignores_note_walk() -> None:
    body = paragraph("Body") + '<w:p><w:r><w:footnoteReference w:id="2"/></w:r></w:p>'
    note = (
        f'<w:footnotes xmlns:w="{NS}"><w:footnote w:id="2">'
        "<w:p><w:r><w:txbxContent>"
        + paragraph("Text in a note box")
        + "</w:txbxContent></w:r></w:p></w:footnote></w:footnotes>"
    )
    data = archive(
        {
            "word/document.xml": (
                f'<w:document xmlns:w="{NS}"><w:body>{body}</w:body></w:document>'
            ).encode(),
            "word/footnotes.xml": note.encode(),
        }
    )

    observed = snapshot(data)

    assert observed["raw_textboxes"] == {"ordinary": 0, "choice": 0, "fallback": 0}
    assert observed["visited_textboxes"] == {}
    assert observed["legacy_text_sha256"] == digest(b"Body\n [przypis 2: Text in a note box] ")


def test_word_textbox_replay_refuses_declarations_before_opening_native_reader() -> None:
    data = archive(
        {
            "word/document.xml": (
                f'<!DOCTYPE w:document [<!ENTITY injected "value">]>'
                f'<w:document xmlns:w="{NS}"><w:body /></w:document>'
            ).encode()
        }
    )

    with pytest.raises(ValueError, match="Unsupported XML encoding or declaration"):
        snapshot(data)

import json
from pathlib import Path

import pytest
from tools.corpus_artifacts import digest
from tools.replay_word_revisions import run

from tests.unit.test_word_note_origins import package


@pytest.mark.parametrize("balanced", [True, False])
@pytest.mark.parametrize("verify_field_ranges", [False, True])
def test_field_replay_distinguishes_physical_text_and_unbalanced_markers(
    tmp_path: Path, balanced: bool, verify_field_ranges: bool
) -> None:
    source = package(
        '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        "<w:r><w:instrText>CODE</w:instrText><w:t>Ordinary before separator</w:t></w:r>"
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>Stored result</w:t></w:r>'
        + ('<w:r><w:fldChar w:fldCharType="end"/></w:r>' if balanced else "")
        + '<w:fldSimple w:instr="CODE"><w:r><w:t>Simple result</w:t></w:r></w:fldSimple></w:p>',
        {},
    )
    corpus = tmp_path / "corpus"
    files = corpus / "documents/files"
    files.mkdir(parents=True)
    (files / "field.docx").write_bytes(source)
    catalog = tmp_path / "catalog.json"
    raw = json.dumps(
        {
            "status": "recorded",
            "suite": "external",
            "observations": {"word/field.docx": {"actual": {"sha256": digest(source)}}},
        }
    ).encode()
    catalog.write_bytes(raw)
    output = tmp_path / "report.json"

    run(
        corpus,
        catalog,
        digest(raw),
        ("field.docx",),
        output,
        profile="fields",
        verify_field_ranges=verify_field_ranges,
    )

    report = json.loads(output.read_text())
    observed = report["observations"]["documents/files/field.docx"]
    assert report["suite"] == "word-field-projection-v1"
    assert observed["noop_text_identical"]
    if balanced:
        assert observed["observations"] == {
            "after_separator": {"mutated": 1, "emitted": 1},
            "before_separator": {"mutated": 1, "emitted": 1},
            "instruction": {"mutated": 1, "emitted": 0},
            "simple_body_text": {"mutated": 1, "emitted": 1},
        }
    else:
        assert observed["field_sequence"] == "unbalanced"
        assert "observations" not in observed
    if verify_field_ranges:
        assert observed["native_field_ranges"] == {
            "state": "checked" if balanced else "unknown",
            "verified_ranges": 3 if balanced else 0,
            "verified_body_ranges": 3 if balanced else None,
        }
    else:
        assert "native_field_ranges" not in observed
    assert (files / "field.docx").read_bytes() == source

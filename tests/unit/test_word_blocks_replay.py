import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

import pytest
from tools import replay_word_blocks

from lexinform.adapters.document_text import DocxTextExtractor
from lexinform.models import WordDetailedExtraction
from tests.unit.test_word_blocks import paragraph, word
from tests.unit.test_word_note_origins import call, entry, note_xml, package


def inputs(tmp_path: Path, *, corrupt: bool = False) -> tuple[Path, Path, str, Path]:
    corpus = tmp_path / "corpus"
    files = corpus / "documents/files"
    files.mkdir(parents=True)
    source = files / "sample.docx"
    source.write_bytes(
        b"not a ZIP" if corrupt else word(paragraph("Art. 1. Termin wynosi 30 dni."))
    )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "suite": "external",
                "status": "recorded",
                "observations": {
                    "word/sample.docx": {
                        "actual": {"sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
                    }
                },
            }
        )
    )
    return corpus, catalog, hashlib.sha256(catalog.read_bytes()).hexdigest(), source


@pytest.mark.parametrize("mode", ["adapter", "parts", "notes", "notes-parts"])
@pytest.mark.parametrize("corrupt", [False, True])
def test_replay_preserves_text_or_explicit_leaf_failure_without_fabricated_blocks(
    tmp_path: Path, corrupt: bool, mode: Literal["adapter", "parts", "notes", "notes-parts"]
) -> None:
    corpus, catalog, sha, source = inputs(tmp_path, corrupt=corrupt)
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    assert replay_word_blocks.run(corpus, catalog, sha, before, mode="library") == 0

    assert (
        replay_word_blocks.run(
            corpus,
            catalog,
            sha,
            after,
            mode=mode,
            reference=before,
            reference_sha256=hashlib.sha256(before.read_bytes()).hexdigest(),
        )
        == 0
    )

    result = json.loads(after.read_bytes())
    assert result["status"] == "passed" and not result["changes"] and not result["errors"]
    assert result["read_inputs"] == {
        "documents/files/sample.docx": hashlib.sha256(source.read_bytes()).hexdigest()
    }
    if corrupt:
        assert result["observations"]["documents/files/sample.docx"]["error_type"] == "BadZipFile"
        assert not result["physical"]
    else:
        assert result["physical"]["documents/files/sample.docx"]["verified_coordinates"] == 1
        assert result["physical"]["documents/files/sample.docx"]["block_counts"]["paragraph"] == 1


def test_replay_rejects_changed_raw_bytes_and_an_unpinned_reference(tmp_path: Path) -> None:
    corpus, catalog, sha, source = inputs(tmp_path)
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    assert replay_word_blocks.run(corpus, catalog, sha, before, mode="library") == 0
    with pytest.raises(ValueError, match="Reference SHA mismatch"):
        replay_word_blocks.run(
            corpus, catalog, sha, after, mode="adapter", reference=before, reference_sha256="0" * 64
        )
    assert not after.exists()
    source.write_bytes(source.read_bytes() + b"\n")

    assert (
        replay_word_blocks.run(
            corpus,
            catalog,
            sha,
            after,
            mode="adapter",
            reference=before,
            reference_sha256=hashlib.sha256(before.read_bytes()).hexdigest(),
        )
        == 1
    )

    result = json.loads(after.read_bytes())
    assert (
        result["status"] == "failed"
        and "SHA mismatch" in result["errors"]["documents/files/sample.docx"]
    )


def test_replay_detects_rebound_internal_alias_after_its_original_target_was_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus, catalog, sha, source = inputs(tmp_path)
    data = source.read_bytes()
    first, second = source.with_name("first.docx"), source.with_name("second.docx")
    source.replace(first)
    source.symlink_to(first)
    second.write_bytes(data + b"\n")
    replacement = source.with_name("replacement")
    replacement.symlink_to(second)
    original = replay_word_blocks.snapshot

    def retarget(
        data: bytes,
        *,
        adapter: bool,
        physical_parts: bool = False,
        with_notes: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        result = original(
            data, adapter=adapter, physical_parts=physical_parts, with_notes=with_notes
        )
        os.replace(replacement, source)
        return result

    monkeypatch.setattr(replay_word_blocks, "snapshot", retarget)
    output = tmp_path / "after.json"

    assert replay_word_blocks.run(corpus, catalog, sha, output, mode="library") == 1

    saved = json.loads(output.read_bytes())
    assert saved["status"] == "failed"
    assert saved["changed_inputs"] == ["documents/files/sample.docx"]


def test_notes_replay_checks_the_declared_target_against_original_xml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus, catalog, sha, source = inputs(tmp_path)
    source.write_bytes(
        package(
            "<w:p>" + call("2") + "</w:p>",
            {"word/footnotes.xml": note_xml("footnote", entry("2", "text"))},
        )
    )
    data = json.loads(catalog.read_bytes())
    data["observations"]["word/sample.docx"]["actual"]["sha256"] = hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    catalog.write_text(json.dumps(data))
    sha = hashlib.sha256(catalog.read_bytes()).hexdigest()
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    assert replay_word_blocks.run(corpus, catalog, sha, before, mode="library") == 0
    real = DocxTextExtractor().extract_with_notes(source.read_bytes())
    bad_note = real.notes.notes[0].model_copy(update={"path": (99,)})
    bad = real.model_copy(update={"notes": real.notes.model_copy(update={"notes": (bad_note,)})})

    def broken_reader(self: DocxTextExtractor, data: bytes) -> WordDetailedExtraction:
        return bad

    monkeypatch.setattr(DocxTextExtractor, "extract_with_notes", broken_reader)

    assert (
        replay_word_blocks.run(
            corpus,
            catalog,
            sha,
            after,
            mode="notes",
            reference=before,
            reference_sha256=hashlib.sha256(before.read_bytes()).hexdigest(),
        )
        == 1
    )
    result = json.loads(after.read_bytes())
    assert result["status"] == "failed" and result["errors"] and not result["physical"]

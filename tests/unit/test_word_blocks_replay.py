import hashlib
import json
from pathlib import Path
from typing import Literal

import pytest
from tools import replay_word_blocks

from tests.unit.test_word_blocks import paragraph, word


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


@pytest.mark.parametrize("mode", ["adapter", "parts"])
@pytest.mark.parametrize("corrupt", [False, True])
def test_replay_preserves_text_or_explicit_leaf_failure_without_fabricated_blocks(
    tmp_path: Path, corrupt: bool, mode: Literal["adapter", "parts"]
) -> None:
    corpus, catalog, sha, _ = inputs(tmp_path, corrupt=corrupt)
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

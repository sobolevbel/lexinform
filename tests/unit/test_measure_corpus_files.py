import hashlib
import json
from pathlib import Path

import pytest
from tools.measure_corpus_files import RAW_ROOTS, measure, run


def corpus_at(root: Path) -> Path:
    root.mkdir()
    (root / "INDEX.json").write_text('{"generated":"2026-10-24","counts":{"prints":1}}')
    for family in ("rcl", "sejm_print"):
        (root / RAW_ROOTS[family]).mkdir(parents=True)
    (root / RAW_ROOTS["rcl"] / "one.pdf").write_bytes(b"same bytes")
    (root / RAW_ROOTS["rcl"] / "two.docx").write_bytes(b"same bytes")
    (root / RAW_ROOTS["sejm_print"] / "alias.PDF").write_bytes(b"same bytes")
    (root / RAW_ROOTS["sejm_print"] / "package.zip").write_bytes(b"different container bytes")
    (root / "research-copy.pdf").write_bytes(b"not a declared raw root")
    return root


def test_global_deduplication_preserves_cross_family_aliases_without_counting_them_twice(
    tmp_path: Path,
) -> None:
    corpus = corpus_at(tmp_path / "corpus")

    result = measure(corpus)

    assert result["totals"] == {
        "files": 4,
        "bytes": 55,
        "unique_contents": 2,
        "unique_bytes": 35,
        "duplicate_files": 2,
        "duplicate_bytes": 20,
    }
    assert result["by_family"]["rcl"]["unique_bytes"] == 10
    assert result["by_family"]["sejm_print"]["unique_bytes"] == 35
    assert result["by_extension"][".pdf"]["files"] == 2
    assert result["cross_family_duplicates"] == [
        {
            "sha256": hashlib.sha256(b"same bytes").hexdigest(),
            "bytes": 10,
            "paths": [
                "documents/files/one.pdf",
                "documents/files/two.docx",
                "sejm/term10/files/alias.PDF",
            ],
        }
    ]
    assert (
        next(row for row in result["inventory"] if row["family"] == "rpw")["state"]
        == "not_available"
    )
    assert "research-copy.pdf" not in [row["path"] for row in result["files"]]


def test_inventory_export_preserves_inputs_and_refuses_to_replace_its_result(
    tmp_path: Path,
) -> None:
    corpus = corpus_at(tmp_path / "corpus")
    before = {p: p.read_bytes() for p in corpus.rglob("*") if p.is_file()}
    output = tmp_path / "inventory.json"

    run(corpus, output)

    result = json.loads(output.read_text())
    assert result["index_sha256"] == hashlib.sha256(before[corpus / "INDEX.json"]).hexdigest()
    assert result["index_counts"] == {"prints": 1}
    assert {p: p.read_bytes() for p in corpus.rglob("*") if p.is_file()} == before
    with pytest.raises(FileExistsError):
        run(corpus, output)
    assert json.loads(output.read_text()) == result
    with pytest.raises(ValueError, match="outside"):
        run(corpus, corpus / "new.json")
    assert not (corpus / "new.json").exists()


@pytest.mark.parametrize("directory", [False, True])
def test_inventory_refuses_symlink_inputs_before_reading_an_outside_file(
    tmp_path: Path, directory: bool
) -> None:
    corpus = corpus_at(tmp_path / "corpus")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.pdf").write_bytes(b"outside data")
    raw = corpus / RAW_ROOTS["rcl"]
    alias = raw / "alias"
    alias.symlink_to(
        outside if directory else outside / "private.pdf", target_is_directory=directory
    )

    with pytest.raises(ValueError, match="symlink"):
        run(corpus, tmp_path / "output.json")

    assert not (tmp_path / "output.json").exists()


def test_absent_raw_bytes_are_not_a_successful_empty_measurement(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "INDEX.json").write_text("{}")

    with pytest.raises(ValueError, match="No raw corpus files"):
        run(corpus, tmp_path / "output.json")

    assert not (tmp_path / "output.json").exists()

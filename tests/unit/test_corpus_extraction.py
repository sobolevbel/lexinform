import gzip
import json
from pathlib import Path

import pytest
from tools import corpus_derivatives
from tools.corpus_extraction import ExtractionCache, bounded_reader

from lexinform.models.documents import ExtractedDocument
from tests.unit.test_corpus_derivatives import cohort
from tests.unit.test_physical_extraction import docx


def test_persistent_cache_reuses_exact_result_across_instances_without_running_extractor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = docx("USTAWA Art. 1. Termin 30 dni.")
    root = tmp_path / "cache"
    first = ExtractionCache(root)
    expected = first.extract_document(data)
    saved = {path.name: path.read_bytes() for path in root.iterdir()}
    second = ExtractionCache(root)

    def unexpected(data: bytes) -> ExtractedDocument:
        raise AssertionError("A matching persistent entry must not re-extract")

    monkeypatch.setattr(second.reader, "extract_document", unexpected)

    assert second.extract_document(data) == expected
    assert (first.hits, first.misses, second.hits, second.misses) == (0, 1, 1, 0)
    assert {path.name: path.read_bytes() for path in root.iterdir()} == saved


def test_raw_options_and_version_changes_produce_distinct_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "cache"
    monkeypatch.setattr(
        "tools.corpus_extraction.extraction_identity",
        lambda: {"options": {"limit": 1}, "version": "v1"},
    )
    first = ExtractionCache(root)
    monkeypatch.setattr(
        "tools.corpus_extraction.extraction_identity",
        lambda: {"options": {"limit": 2}, "version": "v1"},
    )
    options = ExtractionCache(root)
    monkeypatch.setattr(
        "tools.corpus_extraction.extraction_identity",
        lambda: {"options": {"limit": 1}, "version": "v2"},
    )
    version = ExtractionCache(root)
    data = docx("USTAWA")

    first.extract_document(data)
    first.extract_document(docx("USTAWA Art. 1."))
    options.extract_document(data)
    version.extract_document(data)

    assert len(list(root.iterdir())) == 4
    assert first.misses == 2 and options.misses == version.misses == 1


@pytest.mark.parametrize("change", ["result", "raw_sha256", "identity", "compressed"])
def test_corrupt_entries_fail_without_replacing_evidence(tmp_path: Path, change: str) -> None:
    root = tmp_path / "cache"
    data = docx("USTAWA")
    cache = ExtractionCache(root)
    cache.extract_document(data)
    path = next(root.iterdir())
    entry = json.loads(gzip.decompress(path.read_bytes()))
    if change == "result":
        entry["result"]["text"] = "altered"
    elif change == "raw_sha256":
        entry[change] = "0" * 64
    elif change == "identity":
        entry[change]["version"] = "another version"
    payload = b"not gzip" if change == "compressed" else gzip.compress(json.dumps(entry).encode())
    path.write_bytes(payload)

    with pytest.raises(ValueError, match="cache"):
        ExtractionCache(root).extract_document(data)

    assert path.read_bytes() == payload


def test_cache_symlinks_are_refused_and_corpus_output_is_not_allowed(tmp_path: Path) -> None:
    data = docx("USTAWA")
    cache = ExtractionCache(tmp_path / "cache")
    cache.extract_document(data)
    path = next(cache.directory.iterdir())
    outside = tmp_path / "outside"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        cache.extract_document(data)
    alias = tmp_path / "alias"
    alias.symlink_to(cache.directory, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        ExtractionCache(alias)
    corpus, health, pin = cohort(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        corpus_derivatives.run(
            corpus, health, pin, tmp_path / "generation.json", cache=corpus / "cache"
        )


def test_derivatives_use_cache_across_runs_with_identical_observations(tmp_path: Path) -> None:
    corpus, health, pin = cohort(tmp_path)
    cache = tmp_path / "cache"
    first, second = (tmp_path / name for name in ("first.json", "second.json"))
    old = {p: p.read_bytes() for p in corpus.rglob("*") if p.is_file()}

    assert corpus_derivatives.run(corpus, health, pin, first, cache=cache) == 0
    assert corpus_derivatives.run(corpus, health, pin, second, cache=cache) == 0

    before, after = json.loads(first.read_bytes()), json.loads(second.read_bytes())
    assert before["cache"]["misses"] == 1 and before["cache"]["hits"] == 1
    assert after["cache"]["misses"] == 0 and after["cache"]["hits"] == 2
    assert before["counts"] == after["counts"] == {"recovered": 2}
    for row in before["observations"].values():
        row.pop("extraction")
    for row in after["observations"].values():
        row.pop("extraction")
    assert before["observations"] == after["observations"]
    assert {p: p.read_bytes() for p in corpus.rglob("*") if p.is_file()} == old
    assert bounded_reader().extract_document(docx("USTAWA")).text == "USTAWA"

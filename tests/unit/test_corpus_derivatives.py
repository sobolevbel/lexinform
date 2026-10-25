import gzip
import json
from pathlib import Path

import pytest
from tools import corpus_derivatives
from tools.corpus_artifacts import digest
from tools.corpus_health import inspect
from tools.corpus_rcl import CachedExtractor

from tests.unit.test_physical_extraction import archive, docx


def cohort(tmp_path: Path) -> tuple[Path, Path, str]:
    root = tmp_path / "corpus"
    (root / "documents/files").mkdir(parents=True)
    (root / "documents/text").mkdir()
    (root / "corpus").mkdir()
    records = {str(i): {"extension": "zip"} for i in (1, 2)}
    (root / "documents/index.json").write_text(json.dumps(records))
    for identity in records:
        (root / f"documents/files/{identity}.zip").write_bytes(
            archive({"projekt.docx": docx("USTAWA Art. 1. Termin 30 dni.")})
        )
        (root / f"documents/text/{identity}.txt.gz").write_bytes(gzip.compress(b""))
    health = tmp_path / "health.json"
    health.write_text(json.dumps(inspect(root, check_text=True)))
    return root, health, digest(health.read_bytes())


def test_derivative_generation_recovers_text_and_deduplicates_without_overwriting_history(
    tmp_path: Path,
) -> None:
    root, health, pin = cohort(tmp_path)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    target = tmp_path / "generation.json"

    assert corpus_derivatives.run(root, health, pin, target) == 0

    result = json.loads(target.read_bytes())
    assert result["counts"] == {"recovered": 2} and result["unique_text_objects"] == 1
    assert not result["changed_inputs"] and not result["changed_code"]
    row = result["observations"]["1"]
    assert (
        gzip.decompress((tmp_path / row["extraction"]).read_bytes()).decode()
        == "USTAWA Art. 1. Termin 30 dni."
    )
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert CachedExtractor(root).extract(b"1") == ""
    overlay = CachedExtractor(
        root, derivatives=target, derivatives_sha256=digest(target.read_bytes())
    )
    assert overlay.extract(b"1").startswith("USTAWA")
    (root / "documents/files/1.zip").write_bytes(b"different raw version")
    with pytest.raises(ValueError, match="raw source changed"):
        overlay.extract(b"1")
    with pytest.raises(ValueError, match="new and outside"):
        corpus_derivatives.run(root, health, pin, target)


def test_changed_health_or_cached_text_is_not_silently_rebased(tmp_path: Path) -> None:
    root, health, pin = cohort(tmp_path)
    with pytest.raises(ValueError, match="SHA mismatch"):
        corpus_derivatives.run(root, health, "0" * 64, tmp_path / "bad.json")
    (root / "documents/text/1.txt.gz").write_bytes(gzip.compress(b"New cache"))
    with pytest.raises(ValueError, match="cache changed"):
        corpus_derivatives.run(root, health, pin, tmp_path / "changed.json")


def test_oversized_original_is_an_explicit_exclusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, health, pin = cohort(tmp_path)
    monkeypatch.setattr(corpus_derivatives, "MAX_BYTES", 1)
    target = tmp_path / "limited.json"

    assert corpus_derivatives.run(root, health, pin, target) == 0

    result = json.loads(target.read_bytes())
    assert result["counts"] == {"not_run": 2} and result["unique_text_objects"] == 0


def test_old_generation_can_replay_against_its_pinned_index_after_append_only_installation(
    tmp_path: Path,
) -> None:
    root, health, pin = cohort(tmp_path)
    saved = tmp_path / "old-index.json"
    saved.write_bytes((root / "documents/index.json").read_bytes())
    records = json.loads(saved.read_bytes())
    records["3"] = {"extension": "pdf"}
    (root / "documents/index.json").write_text(json.dumps(records))
    with pytest.raises(ValueError, match="index changed"):
        corpus_derivatives.run(root, health, pin, tmp_path / "wrong.json")

    assert (
        corpus_derivatives.run(root, health, pin, tmp_path / "restored.json", index_snapshot=saved)
        == 0
    )

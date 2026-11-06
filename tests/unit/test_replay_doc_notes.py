"""Corpus text references must match both bytes and the exact replay population."""

import gzip
import hashlib
import json
import os
from pathlib import Path

import pytest
from tools.replay_doc_notes import inspect, physical_snapshot, run

from lexinform.models.doc_notes import DocNotedParts
from lexinform.models.extractions import ExtractionResult
from tests.unit.test_doc_note_extraction import source_notes
from tests.unit.test_doc_note_physical_parts import ole_document


def replay_inputs(
    tmp_path: Path, *, wrong_text: bool = False, wrong_population: bool = False
) -> tuple[Path, Path, Path, str]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "INDEX.json").write_bytes(b"{}")
    data = b"non-OLE document"
    (corpus / "file.doc").write_bytes(data)
    identity = hashlib.sha256(data).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "index_sha256": hashlib.sha256(b"{}").hexdigest(),
                "cases": [{"path": "file.doc", "sha256": identity, "extension": ".doc"}],
            }
        )
    )
    reference = tmp_path / "reference.json"
    reference.write_text(
        json.dumps(
            {
                "observations": {
                    "a" * 64 if wrong_population else identity: {
                        "legacy_text_sha256": hashlib.sha256(
                            b"other" if wrong_text else b""
                        ).hexdigest()
                    }
                }
            }
        )
    )
    return corpus, manifest, reference, hashlib.sha256(reference.read_bytes()).hexdigest()


def test_pinned_baseline_records_honest_unavailable_input_without_text_changes(
    tmp_path: Path,
) -> None:
    corpus, manifest, reference, pin = replay_inputs(tmp_path)
    output = tmp_path / "result"

    result = run(
        corpus, manifest, output, source_characters=True, reference=reference, reference_sha256=pin
    )

    assert result["unique_inputs"] == 1
    assert result["status_counts"] == {"unavailable": 1}
    assert result["changed_inputs"] == []
    assert (output / "report.json").is_file()


@pytest.mark.parametrize(
    ("wrong_text", "wrong_population", "reason"),
    [
        (True, False, "Legacy text differs"),
        (False, True, "populations differ"),
    ],
)
def test_reference_mismatch_never_publishes_a_passing_report(
    tmp_path: Path, wrong_text: bool, wrong_population: bool, reason: str
) -> None:
    corpus, manifest, reference, pin = replay_inputs(
        tmp_path, wrong_text=wrong_text, wrong_population=wrong_population
    )
    output = tmp_path / "result"

    with pytest.raises(ValueError, match=reason):
        run(
            corpus,
            manifest,
            output,
            source_characters=True,
            reference=reference,
            reference_sha256=pin,
        )

    assert not output.exists()


def test_changed_reference_bytes_fail_before_replay(tmp_path: Path) -> None:
    corpus, manifest, reference, pin = replay_inputs(tmp_path)
    reference.write_text("{}")

    with pytest.raises(ValueError, match="reference SHA differs"):
        run(
            corpus,
            manifest,
            tmp_path / "result",
            source_characters=True,
            reference=reference,
            reference_sha256=pin,
        )


@pytest.mark.parametrize("relative", [False, True])
def test_manifest_cannot_count_project_sources_as_corpus_inputs(
    tmp_path: Path, relative: bool
) -> None:
    corpus, manifest, _, _ = replay_inputs(tmp_path)
    target = Path(__file__).resolve()
    values = json.loads(manifest.read_text())
    values["cases"][0]["path"] = os.path.relpath(target, corpus) if relative else str(target)
    values["cases"][0]["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(values))
    output = tmp_path / "result"

    with pytest.raises(ValueError, match="escapes root"):
        run(corpus, manifest, output)

    assert not output.exists()


def physical_reference(
    corpus: Path,
    manifest: Path,
    target: Path,
    *,
    wrong_hash: bool = False,
    wrong_population: bool = False,
) -> tuple[Path, str]:
    data = (corpus / "file.doc").read_bytes()
    identity = hashlib.sha256(data).hexdigest()
    snapshot = physical_snapshot(data)
    if wrong_hash:
        snapshot["physical_sha256"] = "f" * 64
    values = json.loads(manifest.read_text())
    payload = {
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "index_sha256": values["index_sha256"],
        "physical_binding": True,
        "paths": [{"path": "other.doc" if wrong_population else "file.doc", "sha256": identity}],
        "observations": {identity: {"physical_binding": snapshot}},
    }
    encoded = gzip.compress(json.dumps(payload).encode(), mtime=0)
    target.write_bytes(encoded)
    return target, hashlib.sha256(encoded).hexdigest()


def test_default_writer_qualification_requires_an_explicit_pinned_physical_baseline(
    tmp_path: Path,
) -> None:
    corpus, manifest, _, _ = replay_inputs(tmp_path)
    output = tmp_path / "result"

    with pytest.raises(ValueError, match="pinned physical reference"):
        run(
            corpus,
            manifest,
            output,
            source_characters=True,
            physical_binding=True,
            default_note_writer=True,
        )

    assert not output.exists()


def test_physical_snapshot_requires_the_explicit_enabled_mode_for_native_default_metadata() -> None:
    data = ole_document(*source_notes())

    with pytest.raises(ValueError, match="expected writer-OFF"):
        physical_snapshot(data)
    result = physical_snapshot(data, default_note_writer=True)

    assert result["default_note_writer"] is True
    assert result["captured_links"] == result["retained_links"] == 1
    assert result["retained_note_bytes"] <= 4096
    assert result["total_summary_bytes"] <= 16384


def test_default_mode_preserves_the_exact_pinned_physical_population(tmp_path: Path) -> None:
    corpus, manifest, reference, text_pin = replay_inputs(tmp_path)
    physical, pin = physical_reference(corpus, manifest, tmp_path / "physical.json.gz")
    output = tmp_path / "result"

    result = run(
        corpus,
        manifest,
        output,
        source_characters=True,
        physical_binding=True,
        default_note_writer=True,
        physical_reference=physical,
        physical_reference_sha256=pin,
        reference=reference,
        reference_sha256=text_pin,
    )

    assert result["unique_inputs"] == 1
    report = json.loads((output / "report.json").read_text())
    assert report["physical_reference_sha256"] == pin
    assert report["default_note_writer"] is True
    assert "four semantic hashes" in report["physical_reference_scope"]


@pytest.mark.parametrize("wrong_population", [False, True])
def test_changed_physical_semantics_or_population_cannot_publish_a_passing_report(
    tmp_path: Path, wrong_population: bool
) -> None:
    corpus, manifest, _, _ = replay_inputs(tmp_path)
    physical, pin = physical_reference(
        corpus,
        manifest,
        tmp_path / "physical.json.gz",
        wrong_hash=not wrong_population,
        wrong_population=wrong_population,
    )
    output = tmp_path / "result"

    with pytest.raises(
        ValueError, match="populations differ" if wrong_population else "semantic hashes differ"
    ):
        run(
            corpus,
            manifest,
            output,
            source_characters=True,
            physical_binding=True,
            default_note_writer=True,
            physical_reference=physical,
            physical_reference_sha256=pin,
        )

    assert not output.exists()


def test_compressed_reference_is_verified_before_decompression(tmp_path: Path) -> None:
    corpus, manifest, _, _ = replay_inputs(tmp_path)
    physical, pin = physical_reference(corpus, manifest, tmp_path / "physical.json.gz")
    physical.write_bytes(b"not a gzip artifact")
    output = tmp_path / "result"

    with pytest.raises(ValueError, match="compressed SHA differs"):
        run(
            corpus,
            manifest,
            output,
            source_characters=True,
            physical_binding=True,
            default_note_writer=True,
            physical_reference=physical,
            physical_reference_sha256=pin,
        )

    assert not output.exists()


def test_unexpected_default_metadata_is_not_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_expected_notes(
        baseline: ExtractionResult, linked: DocNotedParts
    ) -> ExtractionResult:
        return baseline

    monkeypatch.setattr("tools.replay_doc_notes.summarize_doc_notes", missing_expected_notes)

    with pytest.raises(ValueError, match="differs from bounded opt-in"):
        physical_snapshot(ole_document(*source_notes()), default_note_writer=True)


@pytest.mark.parametrize(
    ("main", "reference_cp", "status", "offset"),
    [("A\x02B\r", 1, "anchored", 1), ("A \x02\rB\r", 2, "cleanup_changed", None)],
)
def test_reference_projection_requires_exact_legacy_text_after_marker_removal(
    main: str, reference_cp: int, status: str, offset: int | None
) -> None:
    from_fixture = ole_document(*source_notes(main=main, reference_cp=reference_cp))

    report = inspect(from_fixture, max_notes=256, source_characters=True, reference_projection=True)

    (row,) = report["reference_projection"]
    assert row["status"] == status and row["reference_cp"] == reference_cp
    assert row.get("output_offset") == offset

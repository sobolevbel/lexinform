import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.measure_referenced_files import audit, inventory_contents, run

ROOT_SHA, MEMBER_SHA, INPUT_SHA = "a" * 64, "b" * 64, "c" * 64
MARKER = "NEVER-PRINT-SAVED-ANSWER-TEXT"


def inventory(tmp_path: Path) -> Path:
    path = tmp_path / "inventory.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "corpus": str(tmp_path / "external"),
                "index_sha256": "d" * 64,
                "files": [
                    {"sha256": ROOT_SHA, "bytes": 20, "path": "files/one.zip", "family": "rcl"},
                    {"sha256": ROOT_SHA, "bytes": 20, "path": "files/copy.zip", "family": "print"},
                    {
                        "sha256": MEMBER_SHA,
                        "bytes": 10,
                        "path": "files/member.docx",
                        "family": "rcl",
                    },
                ],
            }
        )
    )
    return path


def database(tmp_path: Path, payloads: list[object]) -> Path:
    path = tmp_path / "state.db"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE analysis_memo (key TEXT PRIMARY KEY, record_json TEXT)")
        db.executemany(
            "INSERT INTO analysis_memo VALUES (?, ?)",
            [(str(i), json.dumps(value)) for i, value in enumerate(payloads)],
        )
    return path


def selection() -> dict[str, object]:
    return {
        "question": "bill_text",
        "state": "unverified",
        "parts": [
            {"members": ["project.docx"], "container_sha256": ROOT_SHA, "sha256": MEMBER_SHA}
        ],
        "observed_parts": [{"sha256": ROOT_SHA}],
    }


def test_copies_and_archive_members_do_not_inflate_referenced_original_bytes(
    tmp_path: Path,
) -> None:
    manifest = inventory(tmp_path)
    path = database(tmp_path, [{"selection": selection()}, {"selection": selection()}])
    before = path.read_bytes()

    report = audit(path, manifest)

    assert report["status"] == "recorded" and not report["findings"]
    assert report["unique_recorded_raw_hashes"] == report["matched_inventory_hashes"] == 1
    assert report["matched_inventory_unique_bytes"] == 20
    assert report["summary"]["raw_reference_occurrences"] == 4
    assert report["references"][0]["families"] == ["print", "rcl"]
    assert path.read_bytes() == before


def test_encoded_snapshots_use_the_common_walker_without_exposing_text(tmp_path: Path) -> None:
    manifest = inventory(tmp_path)
    value = {"analysis": {"summary": MARKER, "selection": selection()}}
    path = database(tmp_path, [{"input_bill_json": json.dumps(value)}])

    report = audit(path, manifest)

    assert report["matched_inventory_unique_bytes"] == 20
    assert MARKER not in json.dumps(report)


@pytest.mark.parametrize("with_selection", [False, True])
def test_text_and_unscoped_legacy_scan_hashes_are_not_treated_as_original_files(
    tmp_path: Path, with_selection: bool
) -> None:
    manifest = inventory(tmp_path)
    value: dict[str, object] = {
        "model": "legacy",
        "prompt_version": "old",
        "created_at": "2026-01-01",
        "analysis": {"summary": MARKER},
        "text_source": "scan",
        "text_sha256": ROOT_SHA,
        "source_url": "https://example.test/one.zip",
    }
    if with_selection:
        value["selection"] = {"question": "bill_text", "state": "unverified", "parts": []}
    path = database(tmp_path, [value])

    report = audit(path, manifest)

    assert report["status"] == "recorded" and report["matched_inventory_unique_bytes"] == 0
    assert report["summary"]["legacy_scan_without_source_manifest"] == 1
    assert not report["references"] and MARKER not in json.dumps(report)


@pytest.mark.parametrize("source_bytes", [20, 21])
def test_scan_source_and_manifested_input_sizes_are_separate(
    tmp_path: Path, source_bytes: int
) -> None:
    manifest = inventory(tmp_path)
    scan = {
        "source_sha256": ROOT_SHA,
        "source_bytes": source_bytes,
        "input_sha256": INPUT_SHA,
        "input_bytes": 5,
    }
    path = database(tmp_path, [{"selection": {"scan_input": scan}}])

    report = audit(path, manifest)

    assert report["matched_inventory_unique_bytes"] == 20
    assert report["scan_input_manifest_unique_bytes"] == 5
    assert report["status"] == ("failed" if source_bytes == 21 else "recorded")
    assert bool(report["findings"]) == (source_bytes == 21)


def test_inventory_rejects_conflicting_sizes_and_repeated_paths(tmp_path: Path) -> None:
    source = json.loads(inventory(tmp_path).read_text())
    for field, value in (("bytes", 21), ("path", "files/one.zip")):
        changed = json.loads(json.dumps(source))
        changed["files"][1][field] = value
        with pytest.raises(ValueError):
            inventory_contents(changed)


def test_empty_scope_is_not_a_successful_zero_footprint(tmp_path: Path) -> None:
    manifest = inventory(tmp_path)
    path = database(tmp_path, [{}])
    assert run(path, manifest, tmp_path / "empty.json") == 2


def test_wrong_inventory_shape_is_rejected_before_output_or_database_use(tmp_path: Path) -> None:
    manifest = tmp_path / "wrong.json"
    manifest.write_text("[]")
    output = tmp_path / "report.json"

    with pytest.raises(ValueError, match="version-1 raw inventory"):
        run(tmp_path / "absent.db", manifest, output)

    assert not output.exists()


def test_outputs_cannot_replace_inputs_or_enter_the_corpus(tmp_path: Path) -> None:
    manifest = inventory(tmp_path)
    path = database(tmp_path, [{"selection": selection()}])
    for target in (path, manifest, tmp_path / "external/report.json"):
        with pytest.raises(ValueError):
            run(path, manifest, target)
    output = tmp_path / "report.json"
    assert run(path, manifest, output) == 0
    before = output.read_bytes()
    with pytest.raises(ValueError):
        run(path, manifest, output)
    assert output.read_bytes() == before

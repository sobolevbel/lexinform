import gzip
import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from tools.legacy_history import audit, run
from tools.saved_records import has_answer_payload

MARKER = "KEEP-LEGACY-ANSWER-CONTENT-OUT-OF-THE-REPORT"


def legacy_record() -> dict[str, object]:
    return {
        "model": "legacy-model",
        "prompt_version": "legacy-prompt",
        "created_at": "2026-01-01T12:00:00Z",
        "analysis": {"summary": MARKER},
        "input_tokens": None,
        "unrecognized_legacy_field": {"preserve": 1},
    }


def database(tmp_path: Path, records: list[str]) -> Path:
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE analysis_memo (key TEXT PRIMARY KEY, record_json TEXT)")
        connection.executemany(
            "INSERT INTO analysis_memo VALUES (?, ?)",
            [(str(index), raw) for index, raw in enumerate(records)],
        )
    return path


def test_exact_values_deduplicate_without_adding_current_model_defaults(tmp_path: Path) -> None:
    first = legacy_record()
    absent = {key: value for key, value in first.items() if key != "input_tokens"}
    path = database(
        tmp_path, [json.dumps(first), json.dumps(first, sort_keys=True), json.dumps(absent)]
    )
    before = path.read_bytes()
    files = sorted(tmp_path.iterdir())
    report = audit(path)
    encoded = [
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        for value in (first, absent)
    ]
    framed = (
        b"["
        + b",".join(sorted(encoded, key=lambda value: hashlib.sha256(value).hexdigest()))
        + b"]"
    )
    assert report["status"] == "recorded" and not report["findings"]
    assert report["summary"]["record_occurrences"] == 3
    assert (
        report["summary"]["unique_records"] == 2 and report["summary"]["duplicate_occurrences"] == 1
    )
    assert report["summary"]["unbound_occurrences"] == 3
    assert report["unique_json_array_bytes"] == len(framed)
    assert report["unique_json_array_gzip_bytes"] == len(gzip.compress(framed, mtime=0))
    assert {row["record_sha256"] for row in report["records"]} == {
        hashlib.sha256(value).hexdigest() for value in encoded
    }
    assert MARKER not in json.dumps(report)
    assert path.read_bytes() == before and sorted(tmp_path.iterdir()) == files


def test_a_recorded_ref_is_observed_without_assigning_or_joining_paid_origin(
    tmp_path: Path,
) -> None:
    record = {**legacy_record(), "computation_id": "opaque-existing-reference"}
    path = database(tmp_path, [json.dumps(record)])
    report = audit(path)
    assert report["status"] == "recorded"
    assert report["summary"]["recorded_ref_occurrences"] == 1
    assert report["records"][0]["has_recorded_result_ref"] is True
    assert "opaque-existing-reference" not in json.dumps(report)


@pytest.mark.parametrize("raw", ["null", "[]", "{}", "true", "1"])
def test_valid_non_record_json_is_not_corruption(tmp_path: Path, raw: str) -> None:
    path = database(tmp_path, [raw])
    report = audit(path)
    assert report["status"] == "not_run" and not report["findings"]
    assert report["summary"]["record_occurrences"] == 0


@pytest.mark.parametrize("raw", ["{invalid", '{"bill_json":"{invalid"}'])
def test_invalid_saved_or_embedded_json_is_reported_without_repair(
    tmp_path: Path, raw: str
) -> None:
    path = database(tmp_path, [raw])
    before = path.read_bytes()
    report = audit(path)
    assert report["status"] == "failed"
    assert any(row["code"] == "invalid_json" for row in report["findings"])
    assert path.read_bytes() == before


def test_nonfinite_record_values_are_not_given_a_canonical_json_identity(tmp_path: Path) -> None:
    record = {**legacy_record(), "unknown_number": float("nan")}
    path = database(tmp_path, [json.dumps(record)])
    report = audit(path)
    assert report["status"] == "failed"
    assert report["findings"][0]["code"] == "invalid_record_json"


def test_metadata_placeholder_is_not_called_an_answer_payload(tmp_path: Path) -> None:
    record = {"model": "", "prompt_version": "", "created_at": None, "digest": None}
    path = database(tmp_path, [json.dumps(record)])
    report = audit(path)
    assert report["records"][0]["has_answer_payload"] is False
    assert not has_answer_payload(record)
    assert has_answer_payload({"review": {"conclusion": "saved"}})


def test_batch_receipts_are_not_promoted_to_answer_bodies(tmp_path: Path) -> None:
    path = database(tmp_path, [])
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE llm_batch_items (result_json TEXT)")
        connection.executemany(
            "INSERT INTO llm_batch_items VALUES (?)",
            [
                (None,),
                (json.dumps({"model": "old", "input_tokens": 30, "error": None}),),
                (json.dumps({"model": "old", "analysis": {"summary": MARKER}}),),
            ],
        )
    report = audit(path)
    assert report["summary"]["batch_item_rows"] == 3
    assert report["summary"]["batch_result_absent"] == 1
    assert report["summary"]["batch_receipt_only"] == 1
    assert report["summary"]["batch_answer_present"] == 1
    assert report["summary"]["record_occurrences"] == 0
    assert MARKER not in json.dumps(report)


def test_report_cannot_overwrite_the_input_database(tmp_path: Path) -> None:
    path = database(tmp_path, [json.dumps(legacy_record())])
    before = path.read_bytes()
    with pytest.raises(ValueError, match="separate"):
        run(path, path)
    assert path.read_bytes() == before


def test_cli_writes_only_the_requested_report(tmp_path: Path) -> None:
    path = database(tmp_path, [json.dumps(legacy_record())])
    before = path.read_bytes()
    output = tmp_path / "out" / "inventory.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.legacy_history",
            "--database",
            str(path),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert json.loads(output.read_text())["summary"]["unique_records"] == 1
    assert MARKER not in completed.stdout + completed.stderr + output.read_text()
    assert path.read_bytes() == before


@pytest.mark.parametrize("field", ["input_bill_json", "source_bill_json"])
def test_frozen_batch_input_snapshots_share_the_computation_doctor_catalogue(
    tmp_path: Path, field: str
) -> None:
    path = database(tmp_path, [])
    metadata = {field: json.dumps({"analysis": legacy_record()})}
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "CREATE TABLE llm_batch_intents (custom_id TEXT PRIMARY KEY, meta_json TEXT)"
        )
        connection.execute(
            "INSERT INTO llm_batch_intents VALUES (?, ?)", ("frozen", json.dumps(metadata))
        )
    report = audit(path)
    assert report["summary"]["record_occurrences"] == 1
    assert report["records"][0]["sources"] == [
        f"llm_batch_intents/frozen/meta_json/{field}/analysis"
    ]
    assert MARKER not in json.dumps(report)

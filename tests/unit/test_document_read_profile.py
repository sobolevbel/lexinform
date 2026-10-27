import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.document_read_profile import audit, run

from lexinform.models import DocumentReadMetrics, HostDocumentReads


def metrics() -> DocumentReadMetrics:
    return DocumentReadMetrics(
        cache_hits=1,
        distinct_bodies=1,
        distinct_body_bytes=10,
        hosts={"source.test": HostDocumentReads(attempts=1, completed=1, body_bytes=10)},
    )


def report(value: object, *, started: str = "2026-10-27T00:30:00+02:00") -> str:
    return json.dumps(
        {"started_at": started, "document_reads": value, "summary": "NEVER-PRINT-BODY"}
    )


def database(tmp_path: Path, reports: list[str | None]) -> Path:
    path = tmp_path / "runs.db"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, report_json TEXT)")
        db.executemany("INSERT INTO runs (report_json) VALUES (?)", [(raw,) for raw in reports])
    return path


def test_daily_totals_keep_legacy_unknowns_and_per_run_distinct_scope(tmp_path: Path) -> None:
    value = metrics().model_dump(mode="json")
    path = database(tmp_path, [report(value), report(value), report(None), None])
    before = path.read_bytes()

    result = audit(path)

    assert result["status"] == "recorded" and not result["findings"]
    assert result["summary"]["measured_reports"] == 2
    assert result["summary"]["unmeasured_reports"] == 1
    assert result["summary"]["rows_without_saved_report"] == 1
    assert result["totals"]["completed_body_bytes"] == 20
    assert result["totals"]["sum_per_run_distinct_body_bytes"] == 20
    assert result["totals"]["within_run_duplicate_body_bytes"] == 0
    assert set(result["by_run_started_day_utc"]) == {"2026-10-26"}
    assert "NEVER-PRINT-BODY" not in json.dumps(result)
    assert path.read_bytes() == before


@pytest.mark.parametrize("measured", [False, True])
def test_observed_zero_is_distinct_from_an_unmeasured_report(
    tmp_path: Path, measured: bool
) -> None:
    raw = DocumentReadMetrics().model_dump(mode="json") if measured else None

    result = audit(database(tmp_path, [report(raw)]))

    assert result["status"] == ("recorded" if measured else "not_run")
    assert result["totals"].get("completed_body_bytes") == (0 if measured else None)


@pytest.mark.parametrize(
    ("raw", "code", "status"),
    [
        ("bad-json", "invalid_run_report", "failed"),
        ("[]", "invalid_run_report", "failed"),
        (report({"schema_version": 1}), "incomplete_document_read_metrics", "failed"),
        (report({"schema_version": 7}), "unsupported_document_read_version", "not_run"),
    ],
)
def test_invalid_and_unsupported_data_do_not_become_false_zero_metrics(
    tmp_path: Path, raw: str, code: str, status: str
) -> None:
    result = audit(database(tmp_path, [raw]))

    assert result["status"] == status
    assert code in {row["code"] for row in result["findings"]}
    assert not result["totals"]


def test_inconsistent_byte_totals_are_rejected(tmp_path: Path) -> None:
    raw = metrics().model_dump(mode="json")
    raw["distinct_body_bytes"] = 11

    result = audit(database(tmp_path, [report(raw)]))

    assert result["status"] == "failed"
    assert result["findings"][0]["code"] == "invalid_document_read_metrics"


@pytest.mark.parametrize("damage", ["missing_host_field", "url_as_host", "boolean_version"])
def test_malformed_measurements_do_not_use_defaults_or_expose_source_urls(
    tmp_path: Path, damage: str
) -> None:
    raw = metrics().model_dump(mode="json")
    if damage == "missing_host_field":
        del raw["hosts"]["source.test"]["failed"]
    elif damage == "url_as_host":
        raw["hosts"] = {"https://source.test/NEVER-PRINT-URL": raw["hosts"]["source.test"]}
    else:
        raw["schema_version"] = True

    result = audit(database(tmp_path, [report(raw)]))

    assert result["status"] == "failed" and not result["totals"]
    assert "NEVER-PRINT-URL" not in json.dumps(result)


def test_undated_measurement_is_not_assigned_a_guessed_utc_day(tmp_path: Path) -> None:
    result = audit(database(tmp_path, [report(metrics().model_dump(), started="2026-10-27")]))

    assert result["status"] == "recorded" and result["summary"]["warnings"] == 1
    assert set(result["by_run_started_day_utc"]) == {"unknown"}
    assert result["totals"]["completed_body_bytes"] == 10


def test_profile_outputs_are_separate_and_immutable(tmp_path: Path) -> None:
    path = database(tmp_path, [report(metrics().model_dump())])
    with pytest.raises(ValueError, match="separate"):
        run(path, path)
    output = tmp_path / "report.json"
    assert run(path, output) == 0
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        run(path, output)
    assert output.read_bytes() == before


def test_unique_body_catalogue_is_separate_from_download_demand_and_raw_retention(
    tmp_path: Path,
) -> None:
    path = database(tmp_path, [report(metrics().model_dump()), report(metrics().model_dump())])
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE artifacts (sha256 TEXT PRIMARY KEY, byte_size INTEGER, recorded_at TEXT)"
        )
        db.executemany(
            "INSERT INTO artifacts VALUES (?, ?, ?)",
            [("a" * 64, 20, "2026-10-27T00:30:00+02:00"), ("b" * 64, 30, "2026-10-28T00:00:00Z")],
        )
    before = path.read_bytes()

    result = audit(path)

    assert result["status"] == "recorded" and not result["findings"]
    assert result["totals"]["completed_body_bytes"] == 20
    catalogue = result["artifact_catalogue"]
    assert catalogue["available"]
    assert catalogue["summary"] == {"known_unique_bodies": 2, "known_unique_body_bytes": 50}
    assert set(catalogue["by_first_recorded_day_utc"]) == {"2026-10-26", "2026-10-28"}
    assert path.read_bytes() == before


@pytest.mark.parametrize("available", [False, True])
def test_missing_catalogue_is_distinct_from_a_known_empty_metadata_table(
    tmp_path: Path, available: bool
) -> None:
    path = database(tmp_path, [report(None)])
    if available:
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                "CREATE TABLE artifacts (sha256 TEXT PRIMARY KEY, byte_size INTEGER, recorded_at TEXT)"
            )

    result = audit(path)

    assert result["status"] == "not_run"
    assert result["artifact_catalogue"]["available"] == available
    assert result["artifact_catalogue"]["summary"].get("known_unique_bodies") == (
        0 if available else None
    )


def test_corrupt_artifact_metadata_does_not_become_a_plausible_byte_estimate(
    tmp_path: Path,
) -> None:
    path = database(tmp_path, [report(metrics().model_dump())])
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE artifacts (sha256 TEXT PRIMARY KEY, byte_size INTEGER, recorded_at TEXT)"
        )
        db.execute("INSERT INTO artifacts VALUES (?, ?, ?)", ("a" * 64, -1, "2026-10-27T00:00:00Z"))

    result = audit(path)

    assert result["status"] == "failed"
    assert result["findings"][0]["code"] == "invalid_artifact_metadata"

import datetime as dt
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tools.source_history_doctor import audit, candidate_trace_inventory, main

from lexinform.adapters.candidate_trace_history import CandidateTraceRowError
from lexinform.adapters.sqlite_repo import SCHEMA_VERSION, SqliteBillRepository
from lexinform.models import (
    ArchiveMemberDisposition,
    ArchiveMemberDispositions,
    CandidateDisposition,
    CandidateDispositionTrace,
    DocumentSelection,
    DocumentSelectionRecord,
    SourceObservation,
    TextDocument,
)
from lexinform.models.candidate_trace import archive_member_rows_sha256
from tests.unit.test_source_history_doctor import MARKER, database


def traced_database(tmp_path: Path) -> tuple[Path, CandidateDispositionTrace, SourceObservation]:
    path = tmp_path / "traced.db"
    document = TextDocument(url="https://source.test/" + MARKER, kind="rcl")
    selection = DocumentSelectionRecord.from_selection(DocumentSelection())
    trace = CandidateDispositionTrace.from_decisions(
        document,
        (
            CandidateDisposition(
                phase="fallback",
                index=0,
                outcome="selected",
                reason="first_acceptable_candidate",
                observed_sha256="a" * 64,
            ),
        ),
        result="selected",
        reason="first_acceptable_candidate",
        selection_state="unverified",
    )
    source = SourceObservation(
        term=10,
        number="historical-owner",
        purpose="analysis",
        document=document,
        selection_id=selection.selection_id,
        recorded_at=dt.datetime(2026, 11, 4, tzinfo=dt.UTC),
    )
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        for number in ("historical-owner", "second"):
            observed = source.model_copy(update={"number": number})
            repo.save_source_observation(selection, observed)
            repo.save_candidate_disposition_trace(observed.observation_id, trace)
    return path, trace, source


def inventory(path: Path) -> dict[str, Any]:
    with closing(sqlite3.connect(path)) as connection:
        before = connection.total_changes
        report = candidate_trace_inventory(connection)
        assert connection.total_changes == before and connection.row_factory is None
        return report


def test_shared_trace_is_one_row_and_two_links_without_current_bill(tmp_path: Path) -> None:
    path, trace, source = traced_database(tmp_path)
    before = path.read_bytes()
    with closing(SqliteBillRepository(path)) as repo:
        assert repo.get_candidate_disposition_trace(trace.trace_id) == trace
        assert repo.list_candidate_disposition_traces(source.observation_id) == [trace]
        dump = repo.dump()
        repo.restore(dump)
        assert repo.dump() == dump
    restored = path.read_bytes()
    report = inventory(path)
    assert report["status"] == "available"
    assert report["trace_rows"] == report["valid_trace_rows"] == 1
    assert report["link_rows"] == report["checked_links"] == 2
    assert not report["findings"]
    assert path.read_bytes() == restored
    assert before
    assert MARKER not in json.dumps(report) and "https://" not in json.dumps(report)


@pytest.mark.parametrize(
    "column,value,code",
    [
        ("trace_json", "bad", "invalid_candidate_trace"),
        ("coverage", "omitted_over_limit", "candidate_trace_identity_mismatch"),
        ("candidate_count", 77, "candidate_trace_identity_mismatch"),
        ("trace_id", "bad", "candidate_trace_identity_mismatch"),
    ],
)
def test_public_readers_and_restore_share_bounded_row_errors(
    tmp_path: Path, column: str, value: str | int, code: str
) -> None:
    path, trace, source = traced_database(tmp_path)
    with closing(SqliteBillRepository(path)) as repo:
        original = repo.dump()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"UPDATE candidate_disposition_traces SET {column}=?", (value,))
        if column == "trace_id":
            connection.execute("UPDATE source_candidate_traces SET trace_id=?", (value,))
    with closing(SqliteBillRepository(path)) as repo:
        bad_dump = repo.dump()
        with pytest.raises(CandidateTraceRowError) as error:
            repo.get_candidate_disposition_trace(
                str(value) if column == "trace_id" else trace.trace_id
            )
        assert error.value.code == code and MARKER not in str(error.value)
        with pytest.raises(CandidateTraceRowError):
            repo.list_candidate_disposition_traces(source.observation_id)
        repo.restore(original)
        with pytest.raises(CandidateTraceRowError):
            repo.restore(bad_dump)
        assert repo.dump() == original
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"UPDATE candidate_disposition_traces SET {column}=?", (value,))
    report = inventory(path)
    assert code in {row["code"] for row in report["findings"]}
    assert report["trace_rows"] == 1 and report["valid_trace_rows"] == 0
    assert MARKER not in json.dumps(report)


@pytest.mark.parametrize(
    "fault,code",
    [
        ("missing_trace", "candidate_trace_parent_missing"),
        ("missing_source", "candidate_trace_source_missing"),
        ("corrupt_source", "candidate_trace_source_invalid"),
    ],
)
def test_dangling_or_invalid_source_links_are_not_validated(
    tmp_path: Path, fault: str, code: str
) -> None:
    path, _, _ = traced_database(tmp_path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            {
                "missing_trace": "DELETE FROM candidate_disposition_traces",
                "missing_source": "DELETE FROM source_observations",
                "corrupt_source": "UPDATE source_observations SET number='mismatch'",
            }[fault]
        )
    report = inventory(path)
    assert code in {row["code"] for row in report["findings"]}
    assert report["link_rows"] == 2 and report["checked_links"] == 0


@pytest.mark.parametrize("layout", ["absent", "trace_columns", "link_columns", "source_columns"])
def test_unavailable_schema_never_looks_like_an_empty_population(
    tmp_path: Path, layout: str
) -> None:
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        if layout != "absent":
            connection.execute(
                "CREATE TABLE candidate_disposition_traces(trace_id, coverage, candidate_count, trace_json)"
            )
            connection.execute("CREATE TABLE source_candidate_traces(observation_id, trace_id)")
        if layout == "trace_columns":
            connection.execute("ALTER TABLE candidate_disposition_traces DROP COLUMN trace_json")
        if layout == "link_columns":
            connection.execute("ALTER TABLE source_candidate_traces DROP COLUMN trace_id")
        if layout == "source_columns":
            connection.execute("CREATE TABLE source_observations(observation_id)")
    report = inventory(path)
    assert report["status"] != "available" and report["unavailable_schema"]
    if layout in ("absent", "trace_columns"):
        assert report["trace_rows"] is report["valid_trace_rows"] is None
    if layout in ("absent", "link_columns"):
        assert report["link_rows"] is report["checked_links"] is None


def test_default_report_is_unchanged_and_opt_in_has_no_body_or_database_write(
    tmp_path: Path,
) -> None:
    path = database(tmp_path)
    before = path.read_bytes()
    original = audit(path)
    normalized = {**original, "database_sha256": "<fixture>", "schema_version": 65}
    assert (
        hashlib.sha256(
            json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == "3a2b5ea524ac08c948f3fd5c89803ebb74e596d094a60186bff40fe9e3504f70"
    )
    expanded = audit(path, include_candidate_traces=True)
    assert expanded.pop("candidate_traces") == inventory(path)
    assert expanded == original
    assert path.read_bytes() == before and MARKER not in json.dumps(expanded)
    assert original["schema_version"] == SCHEMA_VERSION


def test_legacy_unavailable_inventory_does_not_invent_corruption(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA user_version=58")
    report = inventory(path)
    assert report["status"] == "unavailable" and not report["findings"]
    assert report["trace_rows"] is None


def test_opt_in_malformed_source_columns_report_unavailable_and_stay_read_only(
    tmp_path: Path,
) -> None:
    path, _, _ = traced_database(tmp_path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("ALTER TABLE source_observations DROP COLUMN source_kind")
    before = path.read_bytes()
    report = audit(path, include_candidate_traces=True)
    view = report["candidate_traces"]
    assert view["status"] == "partial"
    assert view["link_rows"] == view["unknown_links"] == 2
    assert view["checked_links"] == 0 and view["valid_trace_rows"] == 1
    assert any(row["code"] == "source_history_columns_unavailable" for row in report["findings"])
    assert path.read_bytes() == before and MARKER not in json.dumps(report)


def test_candidate_trace_flag_is_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = database(tmp_path)
    output = tmp_path / "report.json"
    before = path.read_bytes()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "source-history-doctor",
            "--database",
            str(path),
            "--output",
            str(output),
            "--candidate-traces",
        ],
    )
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code == 0
    report = json.loads(output.read_text())
    assert report["candidate_traces"] == inventory(path)
    assert report["candidate_traces"]["trace_rows"] == 0
    assert path.read_bytes() == before


def test_nested_archive_corruption_uses_shared_reads_restore_and_doctor(tmp_path: Path) -> None:
    path, trace, source = traced_database(tmp_path)
    row = ArchiveMemberDisposition(
        stage="policy",
        phase="fallback",
        index=0,
        root_sha256="a" * 64,
        ordinals=(0,),
        physical_part_index=0,
        outcome="selected",
        reason="selected_main",
    )
    members = ArchiveMemberDispositions(
        walk="finished",
        retention="complete",
        observed_rows=1,
        omitted_rows=0,
        rows_sha256=archive_member_rows_sha256((row,)),
        unvisited="none_known",
        rows=(row,),
    )
    mapped = CandidateDispositionTrace.model_validate(
        {**trace.model_dump(), "archive_members": members}
    )
    with closing(SqliteBillRepository(path)) as repo:
        repo.save_candidate_disposition_trace(source.observation_id, mapped)
        original = repo.dump()
    mutation = (
        "UPDATE candidate_disposition_traces SET trace_json="
        "json_set(trace_json,'$.archive_members.observed_rows',2) WHERE trace_id=?"
    )
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(mutation, (mapped.trace_id,))
    with closing(SqliteBillRepository(path)) as repo:
        bad_dump = repo.dump()
        with pytest.raises(CandidateTraceRowError) as error:
            repo.get_candidate_disposition_trace(mapped.trace_id)
        assert error.value.code == "invalid_candidate_trace"
        with pytest.raises(CandidateTraceRowError):
            repo.list_candidate_disposition_traces(source.observation_id)
        repo.restore(original)
        with pytest.raises(CandidateTraceRowError):
            repo.restore(bad_dump)
        assert repo.dump() == original
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(mutation, (mapped.trace_id,))
    before = path.read_bytes()
    report = inventory(path)
    assert report["trace_rows"] == 2 and report["valid_trace_rows"] == 1
    assert "invalid_candidate_trace" in {row["code"] for row in report["findings"]}
    assert MARKER not in json.dumps(report) and "https://" not in json.dumps(report)
    assert path.read_bytes() == before

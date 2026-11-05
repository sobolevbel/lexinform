import datetime as dt
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any, cast

import pytest
from tools.source_history_doctor import audit, main, selection_state_coverage

from lexinform.adapters.sqlite_repo import SCHEMA_VERSION, SqliteBillRepository
from lexinform.document_selection import bill_selection
from lexinform.models import (
    DocumentPart,
    DocumentSelection,
    DocumentSelectionRecord,
    SourceObservation,
    TextDocument,
)
from lexinform.models.documents import SelectionState
from tests.unit.test_source_history_doctor import MARKER, database

NOW = dt.datetime(2026, 11, 4, tzinfo=dt.UTC)
STATES: tuple[SelectionState, ...] = (
    "selected",
    "waiting",
    "ambiguous",
    "unsupported",
    "unverified",
)
UNKNOWN = (
    "unknown_invalid_observation",
    "unknown_missing_selection",
    "unknown_invalid_selection",
    "unknown_identity_mismatch",
)


def selected_record() -> DocumentSelectionRecord:
    text = "Projekt z dnia 1 stycznia 2027 r.\nUSTAWA\nz dnia 1 stycznia 2027 r.\nArt. 1. Termin wynosi 30 dni.\nArt. 2. Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia."
    part = DocumentPart(
        url="https://example.test/" + MARKER,
        role="bill",
        text=text,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )
    chosen = bill_selection(DocumentSelection(parts=(part,)), source_basis=("official_relation",))
    assert chosen.state == "selected", "the pure policy confirms all four required facts"
    return DocumentSelectionRecord.from_selection(chosen)


def missing_annex_record() -> DocumentSelectionRecord:
    text = (
        "Projekt z dnia 1 stycznia 2027 r.\nUSTAWA\nz dnia 1 stycznia 2027 r.\n"
        "Art. 1. Termin wynosi 30 dni.\n"
        "Art. 2. Wzór określa załącznik nr 2 do niniejszej ustawy.\n"
        "Art. 3. Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia."
    )
    part = DocumentPart(
        url="https://example.test/" + MARKER,
        role="bill",
        text=text,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        text_end=len(text),
    )
    chosen = bill_selection(DocumentSelection(parts=(part,)), source_basis=("rcl/1/stage/1",))
    assert chosen.state == "waiting" and "missing_own_annex" in chosen.limitations, (
        "the native producer identifies the explicitly referenced absent annex"
    )
    return DocumentSelectionRecord.from_selection(chosen)


def save_limitation_observation(path: Path, record: DocumentSelectionRecord) -> None:
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_source_observation(
            record,
            SourceObservation(
                term=10,
                number="technical",
                purpose="analysis",
                document=TextDocument(url="https://example.test/" + MARKER, kind="print"),
                selection_id=record.selection_id,
                recorded_at=NOW,
            ),
        )


def limitation_report(path: Path) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        audit(path, include_selection_limitations=True)["selection_limitation_coverage"],
    )


def test_native_missing_annex_has_multilabel_observation_denominator(tmp_path: Path) -> None:
    path = tmp_path / "limitations.db"
    record = missing_annex_record()
    save_limitation_observation(path, record)
    with closing(SqliteBillRepository(path)) as repo:
        for number in ("shared", "damaged"):
            repo.save_source_observation(
                record,
                SourceObservation(
                    term=10,
                    number=number,
                    purpose="review",
                    document=TextDocument(url="https://example.test/" + MARKER, kind="rcl"),
                    selection_id=record.selection_id,
                    recorded_at=NOW,
                ),
            )
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE source_observations SET observation_json='{' WHERE number='damaged'")
    before = path.read_bytes()
    report = limitation_report(path)
    assert report["total"] == 3 and report["known_limitations_rows"] == 2
    assert report["counts"]["missing_own_annex"] == 2
    assert report["counts"]["missing_evidence"] == 2
    assert report["fractions"]["missing_own_annex"] == 2 / 3
    assert sum(report["fractions"].values()) > 1
    assert report["multi_label"] is True and report["unattributed"] == 1
    assert MARKER not in json.dumps(report) and path.read_bytes() == before


def test_repeated_saved_code_counts_once_per_observation(tmp_path: Path) -> None:
    path = tmp_path / "duplicates.db"
    original = missing_annex_record().selection
    repeated = original.model_copy(
        update={"limitations": (*original.limitations, "missing_own_annex")}
    )
    save_limitation_observation(path, DocumentSelectionRecord.from_selection(repeated))
    assert limitation_report(path)["counts"]["missing_own_annex"] == 1


def test_known_empty_limitation_population_has_no_defined_fraction(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
    report = limitation_report(path)
    assert report["status"] == "available" and report["total"] == 0
    assert all(value == 0 for value in report["counts"].values())
    assert all(value is None for value in report["fractions"].values())


@pytest.mark.parametrize("fault", ["observation", "selection", "missing"])
def test_unknown_limitation_rows_stay_in_the_denominator(tmp_path: Path, fault: str) -> None:
    path = tmp_path / "unknown.db"
    save_limitation_observation(path, missing_annex_record())
    with closing(sqlite3.connect(path)) as db, db:
        if fault == "missing":
            db.execute("DELETE FROM document_selections")
        elif fault == "selection":
            db.execute("UPDATE document_selections SET record_json='{' ")
        else:
            db.execute("UPDATE source_observations SET observation_json='{' ")
    report = limitation_report(path)
    assert report["total"] == report["unattributed"] == 1
    assert report["known_limitations_rows"] == 0
    assert report["counts"]["missing_own_annex"] == 0
    assert report["fractions"]["missing_own_annex"] == 0


def test_absent_legacy_limitation_field_does_not_infer_empty_evidence(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    save_limitation_observation(path, selected_record())
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "UPDATE document_selections SET record_json=json_remove(record_json,'$.selection.limitations')"
        )
    report = limitation_report(path)
    assert report["total"] == report["unknown_rows"]["unknown_missing_limitations"] == 1
    assert report["known_limitations_rows"] == report["no_recorded_limitations_rows"] == 0


@pytest.mark.parametrize("table", ["source_observations", "document_selections"])
def test_unavailable_limitation_schema_is_not_known_empty(tmp_path: Path, table: str) -> None:
    path = tmp_path / "schema.db"
    save_limitation_observation(path, missing_annex_record())
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(f"DROP TABLE {table}")
    report = limitation_report(path)
    assert report["status"] == "unavailable"
    assert report["total"] is report["counts"] is report["fractions"] is None


def test_limitation_opt_in_cli_preserves_existing_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "cli.db"
    save_limitation_observation(path, selected_record())
    default = audit(path)
    old = audit(path, include_selection_coverage=True)
    combined = audit(path, include_selection_coverage=True, include_selection_limitations=True)
    report = combined.pop("selection_limitation_coverage")
    assert combined == old and "selection_limitation_coverage" not in default
    assert report["no_recorded_limitations_rows"] == 1
    output = tmp_path / "limitations.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["doctor", "--database", str(path), "--output", str(output), "--selection-limitations"],
    )
    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code == 0
    assert json.loads(output.read_text())["selection_limitation_coverage"] == report


def coverage(path: Path) -> dict[str, Any]:
    with closing(sqlite3.connect(path)) as db:
        return selection_state_coverage(db)


def test_all_states_count_each_observation_even_when_selection_is_shared(tmp_path: Path) -> None:
    path = tmp_path / "states.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        for index, state in enumerate(STATES):
            record = (
                selected_record()
                if state == "selected"
                else DocumentSelectionRecord.from_selection(DocumentSelection(state=state))
            )
            observation = SourceObservation(
                term=10,
                number=str(index),
                purpose="analysis",
                document=TextDocument(url="https://example.test/" + MARKER, kind="print"),
                selection_id=record.selection_id,
                recorded_at=NOW,
            )
            repo.save_source_observation(record, observation)
            repo.save_source_observation(record, observation)
            if state == "waiting":
                shared = observation.model_copy(
                    update={
                        "number": "shared",
                        "purpose": "review",
                        "document": TextDocument(url="https://example.test/" + MARKER, kind="rcl"),
                    }
                )
                repo.save_source_observation(record, shared)
    before = path.read_bytes()
    report = coverage(path)
    assert report["status"] == "available" and report["total"] == 6
    expected: dict[str, int] = {state: 1 for state in STATES}
    expected["waiting"] = 2
    expected.update(dict.fromkeys(UNKNOWN, 0))
    assert report["counts"] == expected
    assert report["fractions"]["waiting"] == 2 / 6
    assert report["fractions"]["selected"] == 1 / 6
    assert sum(report["fractions"].values()) == pytest.approx(1.0)
    groups = {(row["purpose"], row["document_kind"]): row for row in report["groups"]}
    assert groups["analysis", "print"]["total"] == 5
    assert groups["review", "rcl"]["counts"]["waiting"] == 1
    assert groups["review", "rcl"]["fractions"]["waiting"] == 1 / 6
    assert groups["review", "rcl"]["fraction_denominator"] == 6
    assert report["unattributed"] == 0
    assert path.read_bytes() == before
    assert MARKER not in json.dumps(report) and "https://" not in json.dumps(report)


@pytest.mark.parametrize(
    "statement,category",
    [
        ("UPDATE source_observations SET observation_json='bad'", "unknown_invalid_observation"),
        ("DELETE FROM document_selections", "unknown_missing_selection"),
        ("UPDATE document_selections SET record_json='123'", "unknown_invalid_selection"),
        ("UPDATE source_observations SET number='wrong-owner'", "unknown_identity_mismatch"),
        ("UPDATE source_observations SET purpose='review'", "unknown_identity_mismatch"),
        ("UPDATE source_observations SET source_kind='rcl'", "unknown_identity_mismatch"),
        (
            "UPDATE source_observations SET source_url='https://wrong.test'",
            "unknown_identity_mismatch",
        ),
        ("UPDATE document_selections SET state='waiting'", "unknown_identity_mismatch"),
        ("UPDATE document_selections SET question='another'", "unknown_identity_mismatch"),
        (
            "UPDATE document_selections SET record_json=json_set(record_json,'$.selection.question','other')",
            "unknown_identity_mismatch",
        ),
        (
            "UPDATE source_observations SET observation_json=json_set(observation_json,'$.number','other')",
            "unknown_identity_mismatch",
        ),
    ],
)
def test_bad_rows_stay_in_the_denominator_but_have_no_attributed_group(
    tmp_path: Path, statement: str, category: str
) -> None:
    path = database(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(statement)
    before = path.read_bytes()
    report = coverage(path)
    assert report["total"] == report["unattributed"] == 1
    assert report["counts"][category] == report["fractions"][category] == 1
    assert sum(report["counts"].values()) == 1
    assert report["groups"] == []
    assert MARKER not in json.dumps(report) and "wrong-owner" not in json.dumps(report)
    assert "https://" not in json.dumps(report) and path.read_bytes() == before


def test_legacy_selected_without_facts_uses_existing_unverified_decoder(tmp_path: Path) -> None:
    path = database(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "UPDATE document_selections SET record_json=json_set(record_json,'$.selection.state','selected')"
        )
    report = coverage(path)
    assert report["counts"]["unverified"] == 1 and report["counts"]["selected"] == 0
    assert report["unattributed"] == 0


def test_empty_available_tables_do_not_create_observations_or_fractions(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
    report = coverage(path)
    assert report["status"] == "available" and report["total"] == 0
    assert all(value == 0 for value in report["counts"].values())
    assert all(value is None for value in report["fractions"].values())
    assert report["groups"] == [] and report["unattributed"] == 0


@pytest.mark.parametrize("layout", ["absent", "one_table", "missing_columns"])
def test_unavailable_schema_is_explicit_and_never_reports_an_empty_population(
    tmp_path: Path, layout: str
) -> None:
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA user_version=51")
        if layout != "absent":
            db.execute(
                "CREATE TABLE source_observations(observation_id TEXT, observation_json TEXT)"
            )
        if layout == "missing_columns":
            db.execute("CREATE TABLE document_selections(selection_id TEXT, record_json TEXT)")
    report = coverage(path)
    assert report["status"] == "unavailable" and report["total"] is None
    assert report["counts"] is report["fractions"] is report["groups"] is None
    assert report["unavailable_schema"]


def test_default_audit_output_matches_fixed_prechange_bytes_and_opt_in_only_adds_view(
    tmp_path: Path,
) -> None:
    path = database(tmp_path)
    original = audit(path)
    assert original["schema_version"] == SCHEMA_VERSION
    normalized = {**original, "database_sha256": "<fixture>", "schema_version": 65}
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    assert (
        hashlib.sha256(encoded).hexdigest()
        == "3a2b5ea524ac08c948f3fd5c89803ebb74e596d094a60186bff40fe9e3504f70"
    )
    assert audit(path, include_selection_coverage=False) == original
    expanded = audit(path, include_selection_coverage=True)
    assert expanded.pop("selection_state_coverage") == coverage(path)
    assert expanded == original


def test_direct_connection_is_not_reconfigured_and_cli_flag_is_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = database(tmp_path)
    before = path.read_bytes()
    with closing(sqlite3.connect(path)) as db:
        assert db.row_factory is None
        report = selection_state_coverage(db)
        assert db.row_factory is None and db.total_changes == 0
    assert report["total"] == 1
    output = tmp_path / "coverage.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "source-history-doctor",
            "--database",
            str(path),
            "--output",
            str(output),
            "--selection-coverage",
        ],
    )
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code == 0
    assert json.loads(output.read_text())["selection_state_coverage"] == report
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "table,column", [("document_selections", "state"), ("source_observations", "source_kind")]
)
def test_opt_in_damaged_populated_schema_is_unavailable_without_hiding_its_integrity_failure(
    tmp_path: Path, table: str, column: str
) -> None:
    path = database(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    before = path.read_bytes()
    report = audit(path, include_selection_coverage=True)
    assert report["status"] == "failed"
    assert any(
        row["code"] == "source_history_columns_unavailable" and row["ref"] == table
        for row in report["findings"]
    )
    assert report["selection_state_coverage"]["status"] == "unavailable"
    assert report["selection_state_coverage"]["total"] is None
    assert report["selection_state_coverage"]["unavailable_schema"] == [table]
    assert path.read_bytes() == before and MARKER not in json.dumps(report)

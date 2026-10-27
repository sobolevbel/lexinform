import datetime as dt
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.source_history_doctor import audit, run

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    DocumentPart,
    DocumentSelection,
    DocumentSelectionRecord,
    SourceObservation,
    TextDocument,
)

MARKER = "SOURCE-MUST-NOT-APPEAR-IN-DIAGNOSTICS"


def database(tmp_path: Path) -> Path:
    path = tmp_path / "source.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    record = DocumentSelectionRecord.from_selection(
        DocumentSelection(parts=(DocumentPart(sha256="a" * 64, text=MARKER),))
    )
    repo.save_source_observation(
        record,
        SourceObservation(
            term=10,
            number="historical-owner",
            purpose="prefilter",
            document=TextDocument(url="https://source.test/" + MARKER, kind="print"),
            selection_id=record.selection_id,
            recorded_at=dt.datetime(2026, 10, 27, tzinfo=dt.UTC),
        ),
    )
    repo.close()
    return path


def test_valid_source_history_is_read_only_without_requiring_a_current_owner(
    tmp_path: Path,
) -> None:
    path = database(tmp_path)
    before = path.read_bytes()

    report = audit(path)

    assert report["status"] == "recorded" and not report["findings"]
    assert report["summary"]["selection_rows"] == report["summary"]["observation_rows"] == 1
    assert report["summary"]["policy_unknown"] == 1
    assert MARKER not in json.dumps(report) and "https://" not in json.dumps(report)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("statement", "code"),
    [
        ("UPDATE source_observations SET number='another'", "source_observation_columns_mismatch"),
        (
            "UPDATE document_selections SET question='another'",
            "document_selection_columns_mismatch",
        ),
        ("UPDATE source_observations SET observation_json='bad'", "invalid_source_observation"),
        ("UPDATE document_selections SET record_json='123'", "invalid_document_selection"),
        ("DELETE FROM document_selections", "source_selection_unavailable"),
    ],
)
def test_corruption_is_reported_without_fetching_or_reconstructing_a_source(
    tmp_path: Path, statement: str, code: str
) -> None:
    path = database(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(statement)

    report = audit(path)

    assert report["status"] == "failed"
    assert code in {row["code"] for row in report["findings"]}
    assert MARKER not in json.dumps(report)


@pytest.mark.parametrize("schema", [51, 52])
def test_legacy_absence_and_damage_to_the_current_schema_are_distinct(
    tmp_path: Path, schema: int
) -> None:
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(f"PRAGMA user_version={schema}")

    report = audit(path)

    assert report["status"] == ("not_run" if schema == 51 else "failed")
    assert report["summary"]["errors"] == (0 if schema == 51 else 2)


def test_unreferenced_metadata_is_inventory_and_does_not_authorize_deletion(tmp_path: Path) -> None:
    path = database(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DELETE FROM source_observations")
    before = path.read_bytes()

    report = audit(path)

    assert report["summary"]["unreferenced_selection_rows"] == 1
    assert not report["findings"] and path.read_bytes() == before


def test_source_history_outputs_cannot_replace_inputs_or_existing_evidence(tmp_path: Path) -> None:
    path = database(tmp_path)
    with pytest.raises(ValueError, match="separate"):
        run(path, path)
    output = tmp_path / "report.json"
    assert run(path, output) == 0
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        run(path, output)
    assert output.read_bytes() == before

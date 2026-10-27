import datetime as dt
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.source_history_doctor import audit, run

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    ArtifactMetadata,
    ArtifactReadReceipt,
    DocumentPart,
    DocumentSelection,
    DocumentSelectionRecord,
    SelectionArtifactReference,
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


@pytest.mark.parametrize("schema", [51, 52, 53])
def test_legacy_absence_and_damage_to_the_current_schema_are_distinct(
    tmp_path: Path, schema: int
) -> None:
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(f"PRAGMA user_version={schema}")

    report = audit(path)

    assert report["status"] == ("not_run" if schema == 51 else "failed")
    assert report["summary"]["errors"] == {51: 0, 52: 2, 53: 4}[schema]


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


def linked_database(tmp_path: Path) -> Path:
    path = database(tmp_path)
    repo = SqliteBillRepository(path)
    observed = repo.list_source_observations(10, "historical-owner")[0]
    repo.save_artifact_metadata(
        ArtifactMetadata(sha256="a" * 64, byte_size=20, recorded_at=observed.recorded_at)
    )
    repo.save_selection_artifact(
        SelectionArtifactReference(
            selection_id=observed.selection_id,
            part_scope="parts",
            part_index=0,
            artifact_sha256="a" * 64,
        )
    )
    repo.close()
    return path


def test_known_artifact_graph_is_read_only_and_does_not_claim_body_retention(
    tmp_path: Path,
) -> None:
    path = linked_database(tmp_path)
    before = path.read_bytes()

    result = audit(path)

    assert result["status"] == "recorded" and not result["findings"]
    assert result["summary"]["artifact_rows"] == result["summary"]["artifact_link_rows"] == 1
    assert result["summary"]["unlinked_artifact_rows"] == 0
    assert path.read_bytes() == before and MARKER not in json.dumps(result)


@pytest.mark.parametrize(
    ("statement", "code"),
    [
        ("UPDATE artifacts SET recorded_at='2026-10-27'", "invalid_artifact_metadata"),
        ("DELETE FROM artifacts", "artifact_metadata_unavailable"),
        ("UPDATE selection_artifacts SET part_index=1", "artifact_part_out_of_scope"),
        (
            "UPDATE selection_artifacts SET artifact_sha256='" + "b" * 64 + "'",
            "artifact_outer_source_mismatch",
        ),
        (
            "UPDATE selection_artifacts SET selection_id='selection-v1:" + "c" * 64 + "'",
            "artifact_selection_unavailable",
        ),
        ("DROP TABLE artifacts", "artifact_metadata_table_unavailable"),
    ],
)
def test_artifact_damage_is_reported_without_a_source_fetch(
    tmp_path: Path, statement: str, code: str
) -> None:
    path = linked_database(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(statement)

    result = audit(path)

    assert result["status"] == "failed"
    assert code in {item["code"] for item in result["findings"]}
    assert MARKER not in json.dumps(result)


def read_database(tmp_path: Path) -> Path:
    path = linked_database(tmp_path)
    repo = SqliteBillRepository(path)
    repo.save_artifact_read(
        ArtifactReadReceipt(
            sha256="a" * 64,
            byte_size=20,
            requested_url="https://source.test/" + MARKER,
            recorded_at=dt.datetime(2026, 10, 27, tzinfo=dt.UTC),
        )
    )
    repo.close()
    return path


def test_complete_body_receipts_are_checked_without_disclosing_urls(tmp_path: Path) -> None:
    path = read_database(tmp_path)
    before = path.read_bytes()

    result = audit(path)

    assert result["artifact_reads_available"]
    assert result["summary"]["artifact_read_rows"] == 1
    assert result["status"] == "recorded" and not result["findings"]
    assert path.read_bytes() == before and MARKER not in json.dumps(result)


@pytest.mark.parametrize(
    ("statement", "code"),
    [
        ("UPDATE artifact_reads SET read_id='bad'", "artifact_read_identity_mismatch"),
        ("UPDATE artifact_reads SET recorded_at='2026-10-27'", "invalid_artifact_read"),
        ("UPDATE artifact_reads SET requested_url=''", "invalid_artifact_read"),
        ("DELETE FROM artifacts", "artifact_read_body_unavailable"),
        ("UPDATE artifacts SET byte_size=21", "artifact_read_size_mismatch"),
        ("DROP TABLE artifact_reads", "artifact_read_table_unavailable"),
    ],
)
def test_invalid_receipts_and_missing_parents_are_explicit_errors(
    tmp_path: Path, statement: str, code: str
) -> None:
    path = read_database(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(statement)
    before = path.read_bytes()

    result = audit(path)

    assert result["status"] == "failed"
    assert code in {row["code"] for row in result["findings"]}
    assert path.read_bytes() == before and MARKER not in json.dumps(result)


def test_pre_receipt_schema_absence_remains_unknown(tmp_path: Path) -> None:
    path = linked_database(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE artifact_reads")
        db.execute("PRAGMA user_version=53")

    result = audit(path)

    assert not result["artifact_reads_available"] and not result["findings"]

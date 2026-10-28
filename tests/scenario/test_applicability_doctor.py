import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.computation_doctor import audit

from lexinform.models import ComputationResult, ResultApplicabilityObservation
from tests.harness import World
from tests.scenario.test_computation_doctor import computed_world as computed_world
from tests.scenario.test_request_doctor import snapshot

MARKER = "NEVER-PRINT-APPLICABILITY-SOURCE-CONTENT"


def test_source_associations_are_checked_without_emitting_source_metadata(
    computed_world: World, tmp_path: Path
) -> None:
    first = computed_world.repo.list_applicability_observations(10, "3039")[0]
    second = first.model_copy(
        update={
            "source": first.source.model_copy(update={"url": "https://api.test/" + MARKER + ".pdf"})
        }
    )
    computed_world.repo.save_applicability_observation(second)
    path = snapshot(computed_world, tmp_path)
    before = path.read_bytes()
    report = audit(path)
    assert report["status"] == "recorded" and not report["findings"]
    assert report["summary"]["applicability_rows"] == 2
    assert (
        sum(
            ref.startswith("result_applicability/") for ref in report["references"][first.result_id]
        )
        == 2
    )
    assert MARKER not in json.dumps(report) and path.read_bytes() == before


@pytest.mark.parametrize("column", ["observation_id", "result_id", "term", "number"])
def test_sql_and_observation_identity_disagreement_is_reported(
    computed_world: World, tmp_path: Path, column: str
) -> None:
    path = snapshot(computed_world, tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            f"UPDATE result_applicability SET {column}=?", (11 if column == "term" else "changed",)
        )
    before = path.read_bytes()
    report = audit(path)
    assert report["status"] == "failed"
    assert any(row["code"] == "applicability_columns_mismatch" for row in report["findings"])
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "query",
    [
        "UPDATE result_applicability SET observation_json='{}'",
        "UPDATE result_applicability SET observation_json=json_set(observation_json, '$.future_field', 1)",
    ],
)
def test_invalid_or_future_observations_are_not_silently_accepted(
    computed_world: World, tmp_path: Path, query: str
) -> None:
    path = snapshot(computed_world, tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(query)
    report = audit(path)
    assert any(row["code"] == "invalid_applicability_observation" for row in report["findings"])


def test_missing_paid_result_is_reported_without_inventing_it(
    computed_world: World, tmp_path: Path
) -> None:
    path = snapshot(computed_world, tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        raw = db.execute("SELECT observation_json FROM result_applicability").fetchone()[0]
        observation = ResultApplicabilityObservation.model_validate_json(raw).model_copy(
            update={"result_id": "result-v1:" + "f" * 64}
        )
        db.execute(
            "UPDATE result_applicability SET observation_id=?,result_id=?,observation_json=?",
            (observation.observation_id, observation.result_id, observation.model_dump_json()),
        )
    report = audit(path)
    assert any(row["code"] == "applicability_result_unavailable" for row in report["findings"])


def test_failed_paid_result_cannot_support_an_association(
    computed_world: World, tmp_path: Path
) -> None:
    path = snapshot(computed_world, tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        raw = db.execute("SELECT result_json FROM computation_results").fetchone()[0]
        result = ComputationResult.model_validate_json(raw).model_copy(
            update={"validation": "error", "record": None, "error": MARKER}
        )
        db.execute("UPDATE computation_results SET result_json=?", (result.model_dump_json(),))
    report = audit(path)
    assert any(row["code"] == "failed_result_associated" for row in report["findings"])
    assert MARKER not in json.dumps(report)


@pytest.mark.parametrize("version", [50, 51])
def test_absent_new_table_distinguishes_legacy_from_schema_damage(
    computed_world: World, tmp_path: Path, version: int
) -> None:
    path = snapshot(computed_world, tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE result_applicability")
        db.execute(f"PRAGMA user_version={version}")
    report = audit(path)
    assert report["status"] == ("recorded" if version == 50 else "failed")
    assert any(row["code"] == "applicability_table_unavailable" for row in report["findings"]) is (
        version == 51
    )


def test_missing_observation_columns_produce_a_bounded_finding(
    computed_world: World, tmp_path: Path
) -> None:
    path = snapshot(computed_world, tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "ALTER TABLE result_applicability RENAME COLUMN observation_json TO unknown_json"
        )
    report = audit(path)
    assert any(row["code"] == "applicability_columns_unavailable" for row in report["findings"])


def test_historical_owner_absence_does_not_invalidate_the_source_association(
    computed_world: World, tmp_path: Path
) -> None:
    first = computed_world.repo.list_applicability_observations(10, "3039")[0]
    computed_world.repo.save_applicability_observation(first.model_copy(update={"term": 11}))
    path = snapshot(computed_world, tmp_path)
    report = audit(path)
    assert report["status"] == "recorded" and not report["findings"]
    assert report["summary"]["applicability_rows"] == 2

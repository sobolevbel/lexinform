import datetime as dt
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import MIGRATIONS, SqliteBillRepository
from lexinform.models import ResultApplication
from tests.scenario.test_computation_reader import NOW, retained_result


@pytest.fixture
def repository() -> Iterator[SqliteBillRepository]:
    repo = SqliteBillRepository(":memory:")
    repo.migrate()
    yield repo
    repo.close()


def receipt(result_id: str, kind: str, ref: str) -> ResultApplication:
    return ResultApplication.model_validate(
        {
            "result_id": result_id,
            "term": 10,
            "number": "3039",
            "generation": None,
            "target": {"kind": kind, "ref": ref},
            "phase": "projection",
            "outcome": "applied",
            "reason": "retained_result",
            "observed_generation": None,
            "observed_status": None,
            "created_at": NOW,
        }
    )


def test_one_result_can_name_multiple_projection_targets_without_a_primary_generation(
    repository: SqliteBillRepository,
) -> None:
    result = retained_result("amendments")
    repository.save_computation_result(result)
    first = receipt(result.result_id, "status_change", "1")
    second = receipt(result.result_id, "status_change", "2")
    third = receipt(result.result_id, "publication", "1")

    for application in (first, second, third):
        repository.save_result_application(application)
    repository.save_result_application(
        first.model_copy(update={"created_at": NOW + dt.timedelta(hours=1)})
    )
    repository.restore(repository.dump())

    assert repository.list_result_applications(10, "3039") == [first, second, third]
    assert len(repository.list_computation_results(10, "3039")) == 1


@pytest.mark.parametrize("field,value", [("generation", 0), ("phase", "collection")])
def test_an_explicit_target_cannot_borrow_a_primary_generation_or_claim_collection(
    field: str,
    value: int | str,
) -> None:
    application = receipt(retained_result("amendments").result_id, "status_change", "1")
    raw = application.model_dump(mode="json")
    raw[field] = value
    if field == "phase":
        raw.update(outcome="ready", reason="current_generation")

    with pytest.raises(ValidationError, match="target|generation"):
        ResultApplication.model_validate(raw)


def test_unknown_target_namespace_round_trips_without_losing_identity(
    repository: SqliteBillRepository,
) -> None:
    result = retained_result("supplement")
    repository.save_computation_result(result)
    application = receipt(result.result_id, "future_projection", "opaque-reference-v1")

    repository.save_result_application(application)
    repository.restore(repository.dump())

    assert repository.list_result_applications(10, "3039") == [application]


def test_target_identity_is_unique_in_sql_even_with_a_null_generation(
    repository: SqliteBillRepository,
) -> None:
    result = retained_result("amendments")
    repository.save_computation_result(result)
    application = receipt(result.result_id, "status_change", "1")
    repository.save_result_application(application)

    with closing(sqlite3.connect(":memory:")) as database:
        database.executescript(repository.dump())
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            database.execute(
                "INSERT INTO result_applications (result_id, term, number, generation, phase, application_json) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    application.result_id,
                    10,
                    "3039",
                    None,
                    "projection",
                    application.model_dump_json(),
                ),
            )


def test_generationless_receipt_requires_an_explicit_target() -> None:
    application = receipt(retained_result("amendments").result_id, "status_change", "1")
    raw = application.model_dump(mode="json")
    raw.pop("target")

    with pytest.raises(ValidationError, match="generation|target"):
        ResultApplication.model_validate(raw)


@pytest.mark.parametrize(
    "target",
    [
        None,
        {},
        {"kind": "status_change"},
        {"kind": None, "ref": "1"},
        {"kind": "status_change", "ref": ""},
    ],
)
def test_sql_rejects_generationless_rows_without_a_complete_target(
    repository: SqliteBillRepository,
    target: dict[str, str | None] | None,
) -> None:
    result = retained_result("amendments")
    repository.save_computation_result(result)
    application = receipt(result.result_id, "status_change", "1")
    raw = application.model_dump(mode="json")
    raw["target"] = target

    with closing(sqlite3.connect(":memory:")) as database:
        database.executescript(repository.dump())
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            database.execute(
                "INSERT INTO result_applications (result_id, term, number, generation, phase, application_json) VALUES (?, ?, ?, ?, ?, ?)",
                (application.result_id, 10, "3039", None, "projection", json.dumps(raw)),
            )


def test_v49_migration_preserves_original_primary_json_order_and_uniqueness(
    repository: SqliteBillRepository,
) -> None:
    result = retained_result("analysis")
    applications = [
        ResultApplication(
            result_id=result.result_id,
            term=10,
            number="3039",
            generation=generation,
            phase="projection",
            outcome="applied",
            reason="memo_reuse",
            observed_generation=generation,
            observed_status=None,
            created_at=NOW,
        )
        for generation in (0, 1)
    ]
    original_json = [row.model_dump_json() for row in applications]
    assert all('"target"' not in raw for raw in original_json)
    with closing(sqlite3.connect(":memory:")) as legacy:
        for script in MIGRATIONS[:49]:
            legacy.executescript(script)
        legacy.execute(
            "INSERT INTO computation_results VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                result.result_id,
                result.provider,
                result.batch_id,
                result.custom_id,
                10,
                "3039",
                result.model_dump_json(),
            ),
        )
        for rowid, application in zip((11, 29), applications, strict=True):
            legacy.execute(
                "INSERT INTO result_applications (rowid, result_id, term, number, generation, phase, application_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    rowid,
                    result.result_id,
                    10,
                    "3039",
                    application.generation,
                    "projection",
                    application.model_dump_json(),
                ),
            )
        script = "\n".join(legacy.iterdump()) + "\nPRAGMA user_version = 49;\n"

    repository.restore(script)

    assert repository.list_result_applications(10, "3039") == applications
    with closing(sqlite3.connect(":memory:")) as restored:
        restored.executescript(repository.dump())
        rows = restored.execute(
            "SELECT application_json FROM result_applications ORDER BY rowid"
        ).fetchall()
        assert [row[0] for row in rows] == original_json
    for application in applications:
        repository.save_result_application(
            application.model_copy(update={"created_at": NOW + dt.timedelta(days=1)})
        )
    target = receipt(result.result_id, "publication", "1")
    repository.save_result_application(target)
    repository.restore(repository.dump())
    assert repository.list_result_applications(10, "3039") == [*applications, target]

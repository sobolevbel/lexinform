import datetime as dt
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import ResultApplicabilityObservation
from tests.computations import retained_result
from tests.unit.test_applicability import NOW, captured


@pytest.fixture
def repo() -> Iterator[SqliteBillRepository]:
    repository = SqliteBillRepository(":memory:")
    repository.migrate()
    repository.save_computation_result(retained_result("analysis"))
    yield repository
    repository.close()


def test_reader_preserves_future_populated_observations_and_exact_dump(
    repo: SqliteBillRepository,
) -> None:
    first = captured()
    assert repo.get_applicability_observation(first.observation_id) is None
    repo.save_applicability_observation(first)
    before = repo.dump()

    repo.restore(before)

    assert repo.get_applicability_observation(first.observation_id) == first
    assert repo.list_applicability_observations(10, "3039") == [first]
    assert not repo.list_applicability_observations(11, "3039")
    assert repo.dump() == before


def test_later_checks_keep_the_first_timestamp_and_do_not_grow_the_dump(
    repo: SqliteBillRepository,
) -> None:
    first = captured()
    repo.save_applicability_observation(first)
    before = repo.dump()

    repo.save_applicability_observation(
        first.model_copy(update={"created_at": NOW + dt.timedelta(days=200)})
    )

    assert repo.get_applicability_observation(first.observation_id) == first
    assert repo.dump() == before


def test_new_source_versions_and_owner_scopes_do_not_replace_original_facts(
    repo: SqliteBillRepository,
) -> None:
    first = captured()
    second = first.model_copy(
        update={"source": first.source.model_copy(update={"url": "https://api.test/new.pdf"})}
    )
    third = first.model_copy(update={"term": 11})
    for observation in (first, second, third):
        repo.save_applicability_observation(observation)
    assert repo.list_applicability_observations(10, "3039") == [first, second]
    assert repo.list_applicability_observations(11, "3039") == [third]
    assert repo.get_computation_result(first.result_id) == retained_result("analysis")


def test_unknown_paid_results_cannot_be_invented_by_an_observation(
    repo: SqliteBillRepository,
) -> None:
    observation = captured().model_copy(update={"result_id": "result-v1:" + "f" * 64})
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_applicability_observation(observation)
    assert not repo.list_applicability_observations(10, "3039")


def test_invalid_model_copies_are_rejected_before_any_row_is_written(
    repo: SqliteBillRepository,
) -> None:
    first = captured()
    invalid = first.model_copy(
        update={"source": first.source.model_copy(update={"text_sha256": ""})}
    )
    before = repo.dump()
    with pytest.raises(ValueError):
        repo.save_applicability_observation(invalid)
    assert repo.dump() == before


def test_observation_write_belongs_to_the_callers_transaction(repo: SqliteBillRepository) -> None:
    observation = captured()
    before = repo.dump()
    with pytest.raises(RuntimeError, match="observation checkpoint"), repo.atomic():
        repo.save_applicability_observation(observation)
        raise RuntimeError("observation checkpoint failed")
    assert repo.dump() == before


def test_operational_retention_does_not_expire_applicability_history(
    repo: SqliteBillRepository,
) -> None:
    observation = captured()
    repo.save_applicability_observation(observation)
    repo.prune_history(
        before=NOW + dt.timedelta(days=365), memo_before=NOW + dt.timedelta(days=365)
    )
    assert repo.get_applicability_observation(observation.observation_id) == observation
    assert repo.get_computation_result(observation.result_id) == retained_result("analysis")


@pytest.mark.parametrize("column", ["observation_id", "result_id", "term", "number"])
def test_reader_rejects_disagreement_between_sql_and_recorded_identity(
    tmp_path: Path,
    column: str,
) -> None:
    path = tmp_path / "observations.db"
    repository = SqliteBillRepository(path)
    repository.migrate()
    repository.save_computation_result(retained_result("analysis"))
    observation = captured()
    repository.save_applicability_observation(observation)
    repository.close()
    with sqlite3.connect(path) as database:
        if column in ("observation_id", "result_id"):
            database.execute(f"UPDATE result_applicability SET {column}='changed'")
        elif column == "term":
            database.execute("UPDATE result_applicability SET term=11")
        else:
            database.execute("UPDATE result_applicability SET number='3040'")
    repository = SqliteBillRepository(path)
    try:
        with pytest.raises(ValueError, match="columns differ"):
            repository.list_applicability_observations(
                11 if column == "term" else 10, "3040" if column == "number" else "3039"
            )
    finally:
        repository.close()


def test_unknown_json_fields_are_not_silently_reinterpreted() -> None:
    raw = captured().model_dump()
    raw["future_policy"] = "unknown"
    with pytest.raises(ValueError):
        ResultApplicabilityObservation.model_validate(raw)

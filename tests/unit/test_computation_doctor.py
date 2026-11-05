import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.computation_doctor import audit, run

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import ComputationInputBinding, ResultApplication
from lexinform.models.computations import ProjectionReason
from tests.computations import retained_result


def test_projection_census_classifies_saved_receipts_without_changing_default(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    batch = retained_result("analysis")
    bound = batch.model_copy(
        update={"input_binding": ComputationInputBinding(term=10, number="3039", reuse_key="key")}
    )
    legacy = retained_result("reanalysis")
    reasons: tuple[ProjectionReason, ...] = ("batch_result", "memo_reuse", "retained_result")
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_computation_result(bound)
        repo.save_computation_result(legacy)
        collection = ResultApplication(
            result_id=bound.result_id,
            term=10,
            number="3039",
            generation=0,
            outcome="ready",
            reason="current_generation",
            observed_generation=0,
            observed_status=None,
            created_at=bound.collected_at,
        )
        repo.save_result_application(collection)
        for generation, reason in enumerate(reasons):
            repo.save_result_application(
                ResultApplication(
                    result_id=legacy.result_id if reason == "memo_reuse" else bound.result_id,
                    term=10,
                    number="3039",
                    generation=generation,
                    phase="projection",
                    outcome="applied",
                    reason=reason,
                    observed_generation=generation,
                    observed_status=None,
                    created_at=bound.collected_at,
                )
            )
        repo.save_result_application(collection.model_copy(update={"generation": 1}))
        repo.save_result_application(collection.model_copy(update={"generation": 2}))
    with closing(sqlite3.connect(path)) as database, database:
        database.execute(
            "UPDATE result_applications SET application_json='{}'"
            " WHERE phase='collection' AND generation=1"
        )
        database.execute(
            "UPDATE result_applications SET number='wrong'"
            " WHERE phase='collection' AND generation=2"
        )
    before = path.read_bytes()

    default = audit(path, include_supplement_deliveries=True)
    output = tmp_path / "report.json"
    code = run(path, output, include_supplement_deliveries=True, include_projection_receipts=True)
    report = json.loads(output.read_text())
    census = report.pop("projection_receipt_census")

    assert code == 1
    assert report == default
    assert census["status"] == "recorded"
    assert census["counts"] == {
        "application_rows": 6,
        "collection_rows": 1,
        "invalid_rows": 2,
        "projections_by_reason": {
            "batch_result": {"input_binding_known": 1, "input_binding_unknown": 0},
            "memo_reuse": {"input_binding_known": 0, "input_binding_unknown": 1},
            "retained_result": {"input_binding_known": 1, "input_binding_unknown": 0},
        },
    }
    assert path.read_bytes() == before


@pytest.mark.parametrize("missing", [False, True])
def test_projection_census_distinguishes_missing_schema_from_empty_population(
    tmp_path: Path, missing: bool
) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
    if missing:
        with closing(sqlite3.connect(path)) as database, database:
            database.execute("DROP TABLE result_applications")

    report = audit(path, include_projection_receipts=True)

    assert report["status"] == "not_run"
    census = report["projection_receipt_census"]
    assert census["status"] == ("schema_unavailable" if missing else "recorded")
    if missing:
        assert census["counts"] is None
    else:
        assert census["counts"]["application_rows"] == 0

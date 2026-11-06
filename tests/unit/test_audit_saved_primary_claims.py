import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.audit_saved_primary_claims import audit, review_page, run
from tools.corpus_artifacts import digest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import AnalysisRecord, ComputationResult
from tests.computations import retained_result


def test_census_uses_canonical_primary_rows_and_exports_only_claim_addresses(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    output = tmp_path / "report.json"
    result = retained_result("analysis")
    record = result.record
    assert isinstance(record, AnalysisRecord), "the fixture is a primary result"
    claim = "  Exact claim value with spaces.  "
    analysis = record.analysis.model_copy(
        update={
            "key_changes": ["  ", claim],
            "affected_groups": [],
            "effective_date": None,
            "changes_since_previous": [],
        }
    )
    empty = retained_result("reanalysis")
    empty_record = empty.record
    assert isinstance(empty_record, AnalysisRecord), "the fixture is a primary reanalysis"
    empty_analysis = analysis.model_copy(update={"key_changes": []})
    failed = ComputationResult.model_validate(
        {
            **result.model_dump(),
            "custom_id": "failed",
            "validation": "error",
            "record": None,
            "error": "private provider error",
        }
    )
    with closing(SqliteBillRepository(database)) as repo:
        repo.migrate()
        repo.save_computation_result(
            result.model_copy(update={"record": record.model_copy(update={"analysis": analysis})})
        )
        repo.save_computation_result(
            empty.model_copy(
                update={"record": empty_record.model_copy(update={"analysis": empty_analysis})}
            )
        )
        repo.save_computation_result(failed)
        repo.save_computation_result(retained_result("amendments"))
    before = digest(database.read_bytes())

    assert run(database, output) == 0
    report = audit(database)

    assert report["database_sha256"] == digest(database.read_bytes()) == before
    assert report["citation_evidence"] == "legacy_missing"
    assert report["semantic"] == "semantic_not_assessed"
    assert report["row_counts"] == {
        "validated_primary": 2,
        "failed_primary": 1,
        "other_purposes": 1,
        "invalid": 0,
    }
    assert report["nonblank_claim_counts"] == {
        "key_changes": 1,
        "affected_groups": 0,
        "effective_date": 0,
        "changes_since_previous": 0,
    }
    assert report["blank_claim_slots"] == 1
    assert report["answers_without_nonblank_claims"] == 1
    expected_answers: list[dict[str, object]] = [
        {
            "result_id": result.result_id,
            "purpose": "analysis",
            "claims": [
                {"field": "key_changes", "index": 1, "claim_text_sha256": digest(claim.encode())}
            ],
        },
        {"result_id": empty.result_id, "purpose": "reanalysis", "claims": []},
    ]
    assert report["answers"] == sorted(
        expected_answers, key=lambda answer: str(answer["result_id"])
    )
    assert claim.strip() not in output.read_text()
    assert "private provider error" not in output.read_text()


def test_legacy_missing_ledger_and_invalid_rows_have_distinct_outcomes(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    with closing(sqlite3.connect(database)):
        pass
    assert run(database, tmp_path / "legacy.json") == 2
    with closing(SqliteBillRepository(database)) as repo:
        repo.migrate()
        repo.save_computation_result(retained_result("analysis"))
        repo.save_computation_result(retained_result("reanalysis"))
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "UPDATE computation_results SET result_json='private malformed payload' "
            "WHERE custom_id='analysis'"
        )
        db.execute("UPDATE computation_results SET term=11 WHERE custom_id='reanalysis'")

    output = tmp_path / "invalid.json"
    assert run(database, output) == 1
    report = audit(database)

    assert report["invalid_counts"] == {
        "invalid_computation_result": 1,
        "computation_columns_mismatch": 1,
    }
    assert report["result_rows"] == 2
    assert report["answers"] == []
    assert "private malformed payload" not in output.read_text()


def test_review_does_not_use_paid_result_without_its_exact_input(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    result = retained_result("analysis")
    with closing(SqliteBillRepository(database)) as repo:
        repo.migrate()
        repo.save_computation_result(result)

    with pytest.raises(ValueError, match="exact saved batch item"):
        review_page(
            database, database_sha256=digest(database.read_bytes()), result_id=result.result_id
        )

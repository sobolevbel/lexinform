import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tools.computation_doctor import audit

from lexinform.adapters.primary_projections import (
    PrimaryProjectionReader,
    primary_projection_issues,
)
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.json_storage import canonical_json
from lexinform.models import (
    AnalysisRecord,
    BillStatus,
    LocatedText,
    ProcessDetail,
    ReadyAnalysis,
    ResultApplication,
)
from lexinform.primary_projections import PrimaryProjectionError, ProjectionOwner
from lexinform.services.analysis_applications import apply_analysis
from tests.computations import retained_result
from tests.primary_projections import reference

NOW = datetime(2026, 10, 30, tzinfo=UTC)


def test_get_ready_memo_cost_restore_and_cleanup_read_the_same_public_projection(
    tmp_path: Path,
    process_3039: ProcessDetail,
) -> None:
    path = tmp_path / "state.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    repo.upsert_summary(process_3039, now=NOW)
    result = retained_result("analysis").model_copy(update={"generation": 1})
    repo.save_computation_result(result)
    assert isinstance(result.record, AnalysisRecord), "the retained fixture has a primary record"
    current = result.record.model_copy(
        update={
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "revision": 7,
        }
    )
    repo.save_analysis(10, "3039", current)
    repo.save_ready_analysis(
        10,
        "3039",
        ReadyAnalysis(
            record=current,
            located=LocatedText(),
            text="original frozen text",
        ),
    )
    repo.save_analysis_memo(
        "primary", current.model_dump_json(), term=10, number="3039", used_at=NOW
    )
    ref = reference(result).model_copy(
        update={
            "observation": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "revision": 7,
            }
        }
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE bills SET analysis_json=?, ready_analysis_json=?",
            (
                ref.model_dump_json(),
                canonical_json(
                    {
                        "record": ref.model_dump(mode="json"),
                        "located": {},
                        "text": "original frozen text",
                    }
                ),
            ),
        )
        connection.execute("UPDATE analysis_memo SET record_json=?", (ref.model_dump_json(),))
    before = repo.dump()
    for _ in range(2):
        repo.restore(repo.dump())
        bill = repo.get(10, "3039")
        assert bill is not None and bill.analysis == current
        assert bill.ready_analysis is not None and bill.ready_analysis.record == current
        assert bill.ready_analysis.text == "original frozen text"
        assert AnalysisRecord.model_validate_json(repo.load_analysis_memo()["primary"]) == current
        assert repo.most_expensive_analyses(limit=1)[0].analysis == current
        assert repo.dump() == before
    snapshot = tmp_path / "snapshot.db"
    with sqlite3.connect(snapshot) as connection:
        connection.executescript(repo.dump())
    assert audit(snapshot)["summary"]["errors"] == 0
    repo.prune_history(before=NOW + timedelta(days=300), memo_before=NOW + timedelta(days=300))
    assert repo.get_computation_result(result.result_id) == result
    assert repo.get(10, "3039") is not None
    repo.close()


def test_corrupt_reference_doctor_and_restore_preserve_existing_database(
    tmp_path: Path,
    process_3039: ProcessDetail,
) -> None:
    path = tmp_path / "state.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    repo.upsert_summary(process_3039, now=NOW)
    result = retained_result("analysis")
    repo.save_computation_result(result)
    malformed = reference(result).model_copy(update={"retained_record_sha256": "b" * 64})
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE bills SET analysis_json=?", (malformed.model_dump_json(),))
    with pytest.raises(PrimaryProjectionError):
        repo.get(10, "3039")
    with pytest.raises(PrimaryProjectionError):
        repo.most_expensive_analyses(limit=1)
    snapshot = tmp_path / "broken-snapshot.db"
    with sqlite3.connect(snapshot) as connection:
        connection.executescript(repo.dump())
    report = audit(snapshot)
    assert any(item["code"] == "invalid_modern_primary_projection" for item in report["findings"])
    broken = repo.dump()
    target = SqliteBillRepository(":memory:")
    target.migrate()
    clean = target.dump()
    with pytest.raises(PrimaryProjectionError):
        target.restore(broken)
    assert target.dump() == clean
    assert repo.dump() == broken
    repo.close()
    target.close()


def test_missing_owner_and_refs_in_frozen_snapshots_are_rejected_at_known_positions() -> None:
    result = retained_result("analysis")
    raw = reference(result).model_dump_json()
    connection = sqlite3.connect(":memory:")
    reader = PrimaryProjectionReader(connection)
    with pytest.raises(PrimaryProjectionError, match="owner"):
        reader.materialize("analysis_json", raw, None)
    with pytest.raises(PrimaryProjectionError, match="position"):
        reader.materialize("delivery_json", raw, ProjectionOwner(10, "3039"))
    with pytest.raises(PrimaryProjectionError, match="position"):
        reader.materialize("meta_json", canonical_json({"bill_json": raw}), None)
    connection.close()


def test_equal_public_resave_keeps_generation_without_enabling_reference_writer(
    tmp_path: Path,
    process_3039: ProcessDetail,
) -> None:
    path = tmp_path / "state.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    repo.upsert_summary(process_3039, now=NOW)
    result = retained_result("analysis")
    assert isinstance(result.record, AnalysisRecord), "the fixture is a primary result"
    repo.save_computation_result(result)
    repo.save_analysis(10, "3039", result.record)
    before = repo.get(10, "3039")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE bills SET analysis_json=?", (reference(result).model_dump_json(),)
        )
    repo.save_analysis(10, "3039", result.record)
    after = repo.get(10, "3039")
    assert before is not None and after is not None
    assert (
        after.analysis_generation == before.analysis_generation
        and after.analysis == before.analysis
    )
    with sqlite3.connect(path) as connection:
        raw = connection.execute("SELECT analysis_json FROM bills").fetchone()[0]
        assert "modern_primary_ref" not in raw
        assert not list(primary_projection_issues(connection))
    repo.close()


def test_native_link_alias_with_source_receipt_allows_foreign_current_ref(
    tmp_path: Path,
    process_3039: ProcessDetail,
) -> None:
    path = tmp_path / "state.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    source = "RPW/123"
    repo.upsert_summary(process_3039.model_copy(update={"number": source}), now=NOW)
    repo.upsert_summary(process_3039, now=NOW)
    result = retained_result("analysis").model_copy(update={"number": source})
    assert isinstance(result.record, AnalysisRecord), "the source paid result is primary"
    repo.save_computation_result(result)
    apply_analysis(repo, 10, source, result.record, now=NOW)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE bills SET analysis_json=? WHERE number='3039'",
            (reference(result).model_dump_json(),),
        )
    with pytest.raises(PrimaryProjectionError, match="owner bridge"):
        repo.get(10, "3039")
    repo.link_bills(10, source, "3039")
    target = repo.get(10, "3039")
    assert target is not None and target.analysis == result.record
    assert not repo.list_result_applications(10, "3039")
    repo.restore(repo.dump())
    assert repo.get(10, "3039") == target
    repo.close()


def test_first_foreign_application_remains_inline_before_link_alias_or_receipt(
    tmp_path: Path,
    process_3039: ProcessDetail,
) -> None:
    path = tmp_path / "state.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    repo.upsert_summary(process_3039, now=NOW)
    result = retained_result("analysis").model_copy(update={"number": "RPW/123"})
    assert isinstance(result.record, AnalysisRecord), "the source result is a primary analysis"
    repo.save_computation_result(result)
    apply_analysis(repo, 10, "3039", result.record, now=NOW)
    target = repo.get(10, "3039")
    assert target is not None and target.analysis == result.record
    assert repo.list_result_applications(10, "3039")
    with sqlite3.connect(path) as connection:
        raw = connection.execute("SELECT analysis_json FROM bills").fetchone()[0]
        assert "modern_primary_ref" not in raw
    repo.close()


@pytest.mark.parametrize("compact", [False, True])
def test_actual_ready_move_uses_collection_receipt_only_after_known_link(
    tmp_path: Path,
    process_3039: ProcessDetail,
    compact: bool,
) -> None:
    path = tmp_path / "state.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    source = "RPW/123"
    repo.upsert_summary(process_3039.model_copy(update={"number": source}), now=NOW)
    repo.upsert_summary(process_3039, now=NOW)
    result = retained_result("analysis").model_copy(update={"number": source})
    assert isinstance(result.record, AnalysisRecord), "the ready fixture retains a primary result"
    repo.save_computation_result(result)
    ready = ReadyAnalysis(record=result.record, located=LocatedText(), text="frozen input")
    repo.save_ready_analysis(10, source, ready)
    repo.save_result_application(
        ResultApplication(
            result_id=result.result_id,
            term=10,
            number=source,
            generation=0,
            outcome="ready",
            reason="current_generation",
            observed_generation=0,
            observed_status=BillStatus.BATCH_PENDING,
            created_at=NOW,
        )
    )
    if compact:
        with sqlite3.connect(path) as connection:
            value = ready.model_dump(mode="json")
            value["record"] = reference(result).model_dump(mode="json")
            connection.execute(
                "UPDATE bills SET ready_analysis_json=? WHERE number=?",
                (
                    canonical_json(value),
                    source,
                ),
            )
    repo.move_primary_analysis_work(10, source, "3039")
    if compact:
        with pytest.raises(PrimaryProjectionError, match="owner bridge"):
            repo.get(10, "3039")
    else:
        target = repo.get(10, "3039")
        assert target is not None and target.ready_analysis == ready
    repo.link_bills(10, source, "3039")
    target = repo.get(10, "3039")
    assert target is not None and target.ready_analysis == ready
    assert target.analysis is None and not repo.list_result_applications(10, "3039")
    if compact:
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE bills SET analysis_generation=1 WHERE number='3039'")
        with pytest.raises(PrimaryProjectionError, match="generation"):
            repo.get(10, "3039")
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE bills SET analysis_generation=0 WHERE number='3039'")
            connection.execute("DELETE FROM result_applications")
        with pytest.raises(PrimaryProjectionError, match="owner bridge"):
            repo.get(10, "3039")
    repo.close()

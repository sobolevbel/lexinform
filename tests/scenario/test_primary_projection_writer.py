import sqlite3
from datetime import datetime

import pytest

from lexinform.models import AnalysisRecord, BillStatus
from tests.harness import World


@pytest.mark.parametrize("workers", [1, 4])
def test_production_primary_batch_writes_compact_current_and_memo_without_another_model_call(
    workers: int,
) -> None:
    world = World(profile="production", workers=workers)
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    assert world.bill("3039").analysis is None
    assert len(world.batch.submitted) == 1 and not world.llm.contexts
    world.batch.resolve()

    world.run(track=False)

    bill = world.bill("3039")
    assert bill.analysis is not None, "the resolved production batch applies its primary answer"
    assert bill.analysis.computation_id is not None, "the batch answer names its paid result"
    paid = world.repo.get_computation_result(bill.analysis.computation_id)
    assert paid is not None and isinstance(paid.record, AnalysisRecord), (
        "the applied analysis retains a primary result"
    )
    applications = world.repo.list_result_applications(10, "3039")
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(world.repo.dump())
        current = connection.execute("SELECT analysis_json FROM bills").fetchone()[0]
        memos = connection.execute("SELECT record_json FROM analysis_memo").fetchall()
        assert "modern_primary_ref" in current
        assert memos and any("modern_primary_ref" in row[0] for row in memos)
        original_paid = connection.execute(
            "SELECT result_json FROM computation_results WHERE result_id=?", (paid.result_id,)
        ).fetchone()[0]
    world.repo.restore(world.repo.dump())
    assert world.bill("3039") == bill
    world.run(track=False)
    assert len(world.batch.submitted) == 1 and not world.llm.contexts
    assert len(world.publisher.new_bills) == 1
    assert world.repo.get_computation_result(paid.result_id) == paid
    assert world.repo.list_result_applications(10, "3039") == applications
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(world.repo.dump())
        saved_paid = connection.execute(
            "SELECT result_json FROM computation_results WHERE result_id=?", (paid.result_id,)
        ).fetchone()[0]
    assert saved_paid == original_paid


@pytest.mark.parametrize("workers", [1, 4])
def test_primary_memo_sql_failure_rolls_back_collection_before_any_paid_charge(
    workers: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(profile="production", workers=workers)
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    world.batch.resolve()
    batches = world.repo.list_open_llm_batches()
    assert len(batches) == 1
    batch_id = batches[0].batch_id
    analysis = world.container.analysis_service()

    def fail_memo_sql(
        key: str,
        record_json: str,
        *,
        term: int | None = None,
        number: str | None = None,
        used_at: datetime | None = None,
    ) -> None:
        record = AnalysisRecord.model_validate_json(record_json)
        assert record.computation_id is not None, "the real collected record names its paid parent"
        assert world.repo.get_computation_result(record.computation_id) is not None
        assert world.repo.list_result_applications(10, "3039")
        raise sqlite3.OperationalError("injected memo SQL failure")

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, "save_analysis_memo", fail_memo_sql)
        with pytest.raises(sqlite3.OperationalError, match="injected memo SQL failure"):
            analysis.collect_batches()

    assert not world.repo.list_computation_results(10, "3039")
    assert not world.repo.list_result_applications(10, "3039")
    assert not world.repo.load_analysis_memo()
    bill = world.bill("3039")
    assert bill.status is BillStatus.BATCH_PENDING and bill.ready_analysis is None
    item = world.repo.list_llm_batch_items(batch_id)[0]
    assert item.consumed_at is None and item.result is None
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(world.repo.dump())
        assert connection.execute("SELECT accounted_at FROM llm_batch_items").fetchone()[0] is None
    assert not analysis.calls and not world.publisher.new_bills

    analysis.collect_batches()
    after = world.bill("3039")
    assert after.ready_analysis is not None, (
        "the successful collection saves the original ready input"
    )
    assert len(world.repo.list_computation_results(10, "3039")) == 1
    assert len(world.repo.list_result_applications(10, "3039")) == 1
    assert len(analysis.calls) == 1
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(world.repo.dump())
        ready = connection.execute("SELECT ready_analysis_json FROM bills").fetchone()[0]
        memo = connection.execute("SELECT record_json FROM analysis_memo").fetchone()[0]
    assert "modern_primary_ref" not in ready and "modern_primary_ref" in memo
    analysis.collect_batches()
    assert len(analysis.calls) == 1

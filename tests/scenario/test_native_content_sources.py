from typing import Literal

import pytest

from lexinform.models import AnalysisRecord, Bill, ContentSnapshot, StatusChange
from lexinform.models.content import editorial_bill_snapshot
from lexinform.models.primary_projections import PRIMARY_USAGE_FIELDS
from lexinform.services.content_sources import inspect_primary_content_sources
from tests.scenario.test_amendments_command import SENATE
from tests.scenario.test_primary_input_receipts import TEXT, world
from tests.unit.test_physical_extraction import docx


@pytest.mark.parametrize("change", ["none", "nonzero_usage", "observation"])
def test_actual_native_primary_projection_keeps_only_zero_accounting_equivalence(
    change: Literal["none", "nonzero_usage", "observation"],
) -> None:
    current = world(docx(TEXT))
    current.run(track=False)
    current.batch.resolve()
    current.run(track=False)
    original = current.bill("4100")
    record = original.analysis
    assert record is not None and record.computation_id is not None, (
        "the public batch cycle projects its immutable paid primary result"
    )
    paid = current.repo.get_computation_result(record.computation_id)
    assert paid is not None, "primary collection retains its original paid result"
    assert isinstance(paid.record, AnalysisRecord), "the collected primary answer is an analysis"
    assert paid.record.input_tokens is not None, "FakeBatchBackend retains counted primary input"
    assert record != paid.record
    assert tuple(getattr(record, key) for key in PRIMARY_USAGE_FIELDS) == (0, 0, 0, 0)
    bill = original
    if change == "nonzero_usage":
        bill = original.model_copy(
            update={
                "analysis": record.model_copy(update={"input_tokens": paid.record.input_tokens + 1})
            }
        )
    elif change == "observation":
        bill = original.model_copy(
            update={
                "analysis": record.model_copy(update={"source_url": "https://example.test/later"})
            }
        )
    bill_snapshot = editorial_bill_snapshot(bill.model_dump_json())
    if change == "none":
        assert Bill.model_validate_json(bill_snapshot.payload_json).analysis == record
    event = StatusChange(
        term=original.term,
        number=original.number,
        old_fingerprint="before-senate",
        new_fingerprint="native-source-context",
        new_stages=[SENATE],
        detected_at=current.clock.now(),
    )
    change_snapshot = ContentSnapshot(kind="change", payload_json=event.model_dump_json())
    for snapshot in (bill_snapshot, change_snapshot):
        current.repo.save_content_snapshot(snapshot)
    before = current.repo.dump()
    result = inspect_primary_content_sources(
        current.repo,
        bill_snapshot_id=bill_snapshot.snapshot_id,
        change_snapshot_id=change_snapshot.snapshot_id,
    )
    if change == "none":
        assert result.state == "known" and len(result.source_observation_ids) == 1
        assert original.analysis_generation != paid.generation
        applications = current.repo.list_result_applications(original.term, original.number)
        assert any(
            item.phase == "projection"
            and item.generation == paid.generation
            and item.projected_generation == original.analysis_generation
            for item in applications
        )
    elif change == "nonzero_usage":
        assert result.state == "invalid" and result.reason == "paid_record_mismatch"
    else:
        assert result.state == "unknown" and result.reason == "nonfresh_projection"
    assert current.repo.get_computation_result(paid.result_id) == paid
    assert current.repo.dump() == before

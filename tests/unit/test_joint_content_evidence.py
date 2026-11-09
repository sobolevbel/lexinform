import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest

from lexinform.adapters.joint_content_evidence import joint_comparison_application_id
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    Bill,
    ComputationInputBinding,
    ComputationResult,
    JointRecord,
    ProcessDetail,
    ResultApplication,
    ResultApplicationTarget,
    TokenUsage,
    computation_id,
)
from tests.fakes import make_comparison
from tests.formatting import NOW, bill_of


@pytest.mark.parametrize(
    ("change", "accepted"),
    [
        ("paid", True),
        ("memo_reuse", True),
        ("missing_projection", False),
        ("late_projection", False),
        ("foreign_projection", False),
        ("wrong_request", False),
        ("wrong_comparison", False),
        ("wrong_accounting", False),
        ("legacy", False),
    ],
)
def test_joint_reply_requires_its_exact_retained_projection(
    process_3039: ProcessDetail, change: str, accepted: bool
) -> None:
    bill = bill_of(process_3039)
    paid = JointRecord(
        comparison=make_comparison(),
        compared_with=["3038"],
        model="test-model",
        prompt_version="joint",
        created_at=NOW,
        computation_id=computation_id("openai", "batch", "joint"),
        request_sha256="a" * 64,
        input_tokens=20,
        output_tokens=8,
    )
    result = ComputationResult(
        schema_version=2,
        provider="openai",
        batch_id="batch",
        custom_id="joint",
        purpose="joint",
        term=bill.term,
        number=bill.number,
        generation=None,
        collected_at=NOW,
        input_binding=ComputationInputBinding(term=bill.term, number=bill.number, reuse_key="memo"),
        request_sha256=paid.request_sha256,
        validation="validated",
        record=paid,
        model=paid.model,
        usage=TokenUsage(batch_input=20, batch_output=8),
    )
    application = ResultApplication(
        result_id=result.result_id,
        term=bill.term,
        number=bill.number,
        generation=None,
        target=ResultApplicationTarget(kind="bill", ref="joint"),
        phase="projection",
        outcome="applied",
        reason="retained_result",
        observed_generation=None,
        observed_status=None,
        created_at=NOW,
    )
    updates: dict[str, dict[str, object]] = {
        "memo_reuse": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        "wrong_request": {"request_sha256": "b" * 64},
        "wrong_comparison": {"compared_with": ["3040"]},
        "wrong_accounting": {"input_tokens": 1},
        "legacy": {"computation_id": None, "request_sha256": None},
    }
    frozen = Bill.model_validate(
        {**bill.model_dump(), "joint": paid.model_copy(update=updates.get(change, {}))}
    )
    with (
        closing(SqliteBillRepository(":memory:")) as repo,
        closing(sqlite3.connect(":memory:")) as db,
    ):
        repo.migrate()
        repo.save_computation_result(result)
        if change != "missing_projection":
            repo.save_result_application(application)
        db.executescript(repo.dump())
        if change == "late_projection":
            db.execute(
                "UPDATE result_applications SET application_json="
                "json_set(application_json,'$.created_at',?)",
                ((NOW + timedelta(days=1)).isoformat(),),
            )
        if change == "foreign_projection":
            db.execute(
                "UPDATE result_applications SET number='3040', application_json="
                "json_set(application_json,'$.number','3040')"
            )
        found = joint_comparison_application_id(db, frozen, recorded_at=NOW)
    assert found == (1 if accepted else None)

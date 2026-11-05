import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, cast

import pytest
from tools.batch_integrity_doctor import audit, main, run

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import LlmBatch, LlmBatchItem
from lexinform.models.batch import BatchKind
from tests.computations import KINDS, NOW, SHA, retained_result
from tests.unit.test_local_timing_reader import intent_for


def save_batch(repo: SqliteBillRepository, kind: BatchKind, *, count: int = 1) -> None:
    intent = intent_for(kind)
    item = LlmBatchItem.from_intent("provider-batch", intent)
    item = item.model_copy(
        update={"custom_id": kind, "meta": item.meta.model_copy(update={"request_sha256": SHA})}
    )
    repo.save_llm_batch(
        LlmBatch(
            batch_id="provider-batch",
            provider="openai",
            call_kind=kind,
            submitted_at=NOW,
            status="submitted",
            request_count=count,
            estimated_cost_usd=0,
        ),
        [item],
    )


def fixture(path: Path, kind: BatchKind = "analysis") -> None:
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_computation_result(retained_result(kind))
        save_batch(repo, kind)


def mutate(path: Path, sql: str, values: tuple[object, ...] = ()) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(sql, values)


def sections(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], audit(path, include_batch_receipts=True)["sections"])


@pytest.mark.parametrize(
    "fault", ["missing_item", "sha_conflict", "partial_population", "empty_population"]
)
def test_proven_receipt_gaps_are_reported(path: Path, fault: str) -> None:
    fixture(path)
    if fault == "sha_conflict":
        mutate(
            path,
            "UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.request_sha256',?)",
            ("b" * 64,),
        )
    elif fault in ("missing_item", "empty_population"):
        mutate(path, "DELETE FROM llm_batch_items")
    else:
        mutate(path, "UPDATE llm_batches SET request_count=2")
    if fault in ("missing_item", "sha_conflict"):
        with closing(SqliteBillRepository(path)) as repo:
            intent = intent_for("analysis")
            item = LlmBatchItem.from_intent("decoy", intent).model_copy(
                update={"meta": intent.meta.model_copy(update={"request_sha256": SHA})}
            )
            repo.save_llm_batch(
                LlmBatch(
                    batch_id="decoy",
                    provider="openai",
                    call_kind="analysis",
                    submitted_at=NOW,
                    status="submitted",
                    request_count=1,
                    estimated_cost_usd=0,
                ),
                [item],
            )
    report = audit(path, include_batch_receipts=True)
    code = (
        "paid_receipt_sha_mismatch"
        if fault == "sha_conflict"
        else "paid_receipt_missing"
        if fault == "missing_item"
        else "batch_item_deficit"
    )
    assert any(row["code"] == code for row in report["findings"])


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "state.db"


@pytest.mark.parametrize("kind", KINDS)
def test_exact_all_purposes_survive_consumption_forgetting_and_foreign_owner(
    path: Path, kind: BatchKind
) -> None:
    fixture(path, kind)
    with closing(SqliteBillRepository(path)) as repo:
        repo.mark_llm_batch_item_consumed("provider-batch", kind, consumed_at=NOW)
        repo.mark_llm_batch_forgotten("provider-batch", forgotten_at=NOW)
    mutate(path, "UPDATE llm_batch_items SET number='different-current-owner'")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    report = audit(path, include_batch_receipts=True)
    assert report["summary"] == {"errors": 0, "warnings": 0}
    assert report["sections"]["paid_receipts"]["matched_rows"] == 1
    assert report["sections"]["batch_populations"]["equal_rows"] == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("side", ["result", "item", "count"])
def test_legacy_absent_identity_is_unknown_not_corruption(path: Path, side: str) -> None:
    fixture(path)
    if side == "result":
        mutate(
            path,
            "UPDATE computation_results SET result_json=json_remove(result_json,'$.request_sha256','$.record.request_sha256')",
        )
    elif side == "item":
        mutate(
            path, "UPDATE llm_batch_items SET meta_json=json_remove(meta_json,'$.request_sha256')"
        )
    else:
        mutate(path, "ALTER TABLE llm_batches DROP COLUMN request_count")
    result = sections(path)
    if side == "count":
        assert result["batch_populations"]["assessed_rows"] is None
        assert result["batch_populations"]["rows"] is None
    else:
        assert result["paid_receipts"]["sha_unknown_rows"] == 1
        assert result["paid_receipts"]["sha_mismatch_rows"] == 0


@pytest.mark.parametrize("table", ["computation_results", "llm_batches", "llm_batch_items"])
def test_missing_schema_is_not_empty_receipt_coverage(path: Path, table: str) -> None:
    fixture(path)
    mutate(path, f"DROP TABLE {table}")
    report = sections(path)
    assert report["paid_receipts"]["matched_rows"] is None
    assert report["paid_receipts"]["rows"] == (None if table == "computation_results" else 1)
    if table != "computation_results":
        assert report["batch_populations"]["assessed_rows"] is None


@pytest.mark.parametrize(
    "fault", ["excess", "negative_count", "provider", "deep_result", "bad_item"]
)
def test_known_corruption_is_bounded(path: Path, fault: str) -> None:
    fixture(path)
    sql = {
        "excess": "UPDATE llm_batches SET request_count=0",
        "negative_count": "UPDATE llm_batches SET request_count=-1",
        "provider": "UPDATE llm_batches SET provider='anthropic'",
        "deep_result": "UPDATE computation_results SET result_json=?",
        "bad_item": "UPDATE llm_batch_items SET meta_json=?",
    }[fault]
    mutate(
        path,
        sql,
        ("[" * 2000 + "]" * 2000,)
        if fault == "deep_result"
        else ('{"secret":"DO-NOT-EMIT"}',)
        if fault == "bad_item"
        else (),
    )
    report = audit(path, include_batch_receipts=True)
    assert report["summary"]["errors"] > 0
    assert "DO-NOT-EMIT" not in json.dumps(report)
    assert report["sections"]["paid_receipts"]["rows"] == 1


def test_opt_in_empty_and_default_object_are_separate(
    path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
    default = audit(path)
    assert default["profile"] == "saved-batch-application-integrity-v1"
    assert "paid_receipts" not in default["sections"]
    report = audit(path, include_batch_receipts=True)
    assert report["sections"]["paid_receipts"]["rows"] == 0
    assert report["sections"]["batch_populations"]["equal_rows"] == 0
    output = tmp_path / "report.json"
    assert run(path, output, include_batch_receipts=True) == 0
    assert json.loads(output.read_text()) == report
    cli_output = tmp_path / "cli.json"
    monkeypatch.setattr(
        "sys.argv",
        ["doctor", "--database", str(path), "--output", str(cli_output), "--batch-receipts"],
    )
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code == 0
    assert json.loads(cli_output.read_text()) == report


@pytest.mark.parametrize("known_sha", [True, False])
def test_known_item_purpose_conflict_is_not_a_paid_receipt(path: Path, known_sha: bool) -> None:
    fixture(path)
    meta = intent_for("name").meta.model_copy(update={"request_sha256": SHA})
    mutate(
        path, "UPDATE llm_batch_items SET call_kind='name',meta_json=?", (meta.model_dump_json(),)
    )
    if not known_sha:
        mutate(
            path,
            "UPDATE computation_results SET result_json=json_remove(result_json,'$.request_sha256','$.record.request_sha256')",
        )
    report = audit(path, include_batch_receipts=True)
    assert any(
        row["code"] == "paid_receipt_purpose_mismatch" and row["level"] == "error"
        for row in report["findings"]
    )
    assert report["sections"]["paid_receipts"]["purpose_mismatch_rows"] == 1
    assert report["sections"]["paid_receipts"]["matched_rows"] == 0


def test_mixed_batch_header_does_not_override_actual_item_purpose(path: Path) -> None:
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        items = []
        kinds: tuple[BatchKind, ...] = ("analysis", "name")
        for kind in kinds:
            result = retained_result(kind)
            repo.save_computation_result(result)
            intent = intent_for(kind)
            items.append(
                LlmBatchItem.from_intent("provider-batch", intent).model_copy(
                    update={
                        "custom_id": kind,
                        "meta": intent.meta.model_copy(update={"request_sha256": SHA}),
                    }
                )
            )
        repo.save_llm_batch(
            LlmBatch(
                batch_id="provider-batch",
                provider="openai",
                call_kind="analysis",
                submitted_at=NOW,
                status="submitted",
                request_count=2,
                estimated_cost_usd=0,
            ),
            items,
        )
        for item in items:
            repo.mark_llm_batch_item_consumed(item.batch_id, item.custom_id, consumed_at=NOW)
    report = audit(path, include_batch_receipts=True)
    assert report["summary"] == {"errors": 0, "warnings": 0}
    assert report["sections"]["paid_receipts"]["matched_rows"] == 2
    assert report["sections"]["batch_populations"]["equal_rows"] == 1

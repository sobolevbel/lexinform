import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.batch_integrity_doctor import audit, main, run

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import BillStatus, LlmBatch, LlmBatchItem, ProcessSummary, ResultApplication
from lexinform.models.batch import BatchKind
from tests.computations import KINDS, NOW, retained_result
from tests.unit.test_local_timing_reader import intent_for


def owner(repo: SqliteBillRepository) -> None:
    repo.upsert_summary(
        ProcessSummary(
            term=10,
            number="3039",
            title="Technical saved owner",
            document_type="projekt ustawy",
            change_date=NOW.date(),
        ),
        now=NOW,
    )


def application() -> ResultApplication:
    return ResultApplication(
        result_id=retained_result("analysis").result_id,
        term=10,
        number="3039",
        generation=0,
        phase="collection",
        outcome="ready",
        reason="current_generation",
        observed_generation=0,
        observed_status=None,
        created_at=NOW,
    )


def duplicate_database(path: Path) -> None:
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_computation_result(retained_result("analysis"))
        repo.save_result_application(application())
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DROP INDEX ux_result_application_generation")
        connection.execute(
            "INSERT INTO result_applications(result_id,term,number,generation,phase,application_json) SELECT result_id,term,number,generation,phase,application_json FROM result_applications"
        )
        connection.commit()


def test_duplicate_application_identity_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    duplicate_database(path)
    report = audit(path)
    assert any(item["code"] == "duplicate_application_identity" for item in report["findings"])


@pytest.mark.parametrize("kind", ["analysis", "reanalysis"])
@pytest.mark.parametrize("carrier", ["queued", "submitting", "item"])
def test_public_reset_stale_queued_generation_is_an_advisory(
    tmp_path: Path, kind: BatchKind, carrier: str
) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        owner(repo)
        save_active(repo, kind, carrier)
        repo.reset_bill(10, "3039", BillStatus.ANALYSIS_PENDING)
    report = audit(path)
    assert any(
        item["code"] == "active_primary_generation_mismatch" and item["level"] == "warning"
        for item in report["findings"]
    )
    assert report["summary"]["errors"] == 0
    section = "llm_batch_items" if carrier == "item" else "llm_batch_intents"
    assert report["sections"][section]["assessed_rows"] == 1
    assert report["sections"][section]["stale_generation_rows"] == 1


def save_active(
    repo: SqliteBillRepository,
    kind: BatchKind = "analysis",
    carrier: str = "queued",
    *,
    consumed: bool = False,
) -> None:
    intent = intent_for(kind)
    if carrier != "item":
        repo.save_batch_intent(intent.model_copy(update={"state": carrier}))
        return
    item = LlmBatchItem.from_intent("technical-batch", intent)
    repo.save_llm_batch(
        LlmBatch(
            batch_id="technical-batch",
            provider="openai",
            call_kind=kind,
            submitted_at=NOW,
            status="submitted",
            request_count=1,
            estimated_cost_usd=0,
        ),
        [item],
    )
    if consumed:
        repo.mark_llm_batch_item_consumed("technical-batch", item.custom_id, consumed_at=NOW)


def test_distinct_phase_generation_and_opaque_targets_are_not_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        primary = retained_result("analysis")
        secondary = retained_result("supplement")
        repo.save_computation_result(primary)
        repo.save_computation_result(secondary)
        base = application()
        applied = base.model_copy(
            update={"phase": "projection", "outcome": "applied", "reason": "memo_reuse"}
        )
        for row in (base, applied, applied.model_copy(update={"generation": 1})):
            repo.save_result_application(row)
        for kind, ref in (
            ("future_projection", "one"),
            ("future_projection", "two"),
            ("other_projection", "one"),
        ):
            target = ResultApplication.model_validate(
                {
                    **applied.model_dump(),
                    "result_id": secondary.result_id,
                    "generation": None,
                    "target": {"kind": kind, "ref": ref},
                }
            )
            repo.save_result_application(target)
    report = audit(path)
    assert report["findings"] == []
    assert report["sections"]["applications"]["rows"] == 6
    assert report["sections"]["applications"]["duplicate_groups"] == 0


def test_nullable_target_duplicate_uses_actual_sql_target_key(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        secondary = retained_result("supplement")
        repo.save_computation_result(secondary)
        target = ResultApplication.model_validate(
            {
                **application().model_dump(),
                "result_id": secondary.result_id,
                "generation": None,
                "phase": "projection",
                "outcome": "applied",
                "reason": "retained_result",
                "target": {"kind": "future_projection", "ref": "opaque"},
            }
        )
        repo.save_result_application(target)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DROP INDEX ux_result_application_target")
        connection.execute(
            "INSERT INTO result_applications(result_id,term,number,generation,phase,application_json) SELECT result_id,term,number,generation,phase,application_json FROM result_applications"
        )
        connection.commit()
    report = audit(path)
    assert report["sections"]["applications"]["duplicate_groups"] == 1
    assert report["sections"]["applications"]["duplicate_rows"] == 2


def test_consumed_items_and_historical_applications_are_not_current_generation_checks(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        owner(repo)
        repo.save_computation_result(retained_result("analysis"))
        repo.save_result_application(application())
        save_active(repo, carrier="item", consumed=True)
        repo.reset_bill(10, "3039", BillStatus.ANALYSIS_PENDING)
    report = audit(path)
    assert report["findings"] == []
    assert report["sections"]["applications"]["rows"] == 1
    assert report["sections"]["llm_batch_items"]["rows"] == 0


def test_missing_saved_generation_does_not_infer_model_default_zero(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        owner(repo)
        save_active(repo)
        repo.reset_bill(10, "3039", BillStatus.ANALYSIS_PENDING)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "UPDATE llm_batch_intents SET meta_json=json_remove(meta_json,'$.generation')"
        )
        connection.commit()
    report = audit(path)
    section = report["sections"]["llm_batch_intents"]
    assert section["generation_unknown_rows"] == 1 and section["assessed_rows"] == 0
    assert not any(
        row["code"] == "active_primary_generation_mismatch" for row in report["findings"]
    )


def test_all_nonprimary_purposes_are_explicitly_not_applicable(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        owner(repo)
        for kind in KINDS:
            if kind not in ("analysis", "reanalysis"):
                save_active(repo, kind)
    report = audit(path)
    section = report["sections"]["llm_batch_intents"]
    assert section["not_applicable_rows"] == 8
    assert section["primary_rows"] == section["generation_unknown_rows"] == 0
    assert report["findings"] == []


def test_missing_owner_is_unknown_without_cross_owner_inference(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        save_active(repo)
    report = audit(path)
    section = report["sections"]["llm_batch_intents"]
    assert section["owner_unknown_rows"] == 1 and section["assessed_rows"] == 0
    assert report["findings"][0]["code"] == "active_primary_owner_unknown"


@pytest.mark.parametrize(
    "table", ["result_applications", "llm_batch_intents", "llm_batch_items", "bills"]
)
def test_missing_schema_is_not_a_known_empty_population(tmp_path: Path, table: str) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(f"DROP TABLE {table}")
        connection.commit()
    report = audit(path)
    assert report["status"] == "partial"
    if table == "bills":
        for name in ("llm_batch_intents", "llm_batch_items"):
            section = report["sections"][name]
            assert section["rows"] == 0 and section["assessed_rows"] is None
            assert (
                section["same_generation_rows"] is None and section["stale_generation_rows"] is None
            )
    else:
        name = "applications" if table == "result_applications" else table
        section = report["sections"][name]
        assert section["status"] == "unavailable"
        assert all(
            value is None
            for key, value in section.items()
            if key not in ("status", "missing_columns")
        )


@pytest.mark.parametrize("carrier", ["application", "queued", "item"])
def test_bad_json_is_bounded_and_does_not_drop_row_denominator(
    tmp_path: Path, carrier: str
) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        owner(repo)
        if carrier == "application":
            repo.save_computation_result(retained_result("analysis"))
            repo.save_result_application(application())
        else:
            save_active(repo, carrier=carrier)
    table = (
        "result_applications"
        if carrier == "application"
        else "llm_batch_items"
        if carrier == "item"
        else "llm_batch_intents"
    )
    column = "application_json" if carrier == "application" else "meta_json"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(f"UPDATE {table} SET {column}=?", ("[" * 2000 + "0" + "]" * 2000,))
        connection.commit()
    before = path.read_bytes()
    report = audit(path)
    section = report["sections"]["applications" if carrier == "application" else table]
    assert section["rows"] == section["invalid_rows"] == 1
    assert report["summary"]["errors"] == 1
    assert path.read_bytes() == before


def test_empty_available_schema_and_cli_are_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    output = tmp_path / "report.json"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
    before = path.read_bytes()
    report = audit(path)
    assert report["status"] == "recorded" and report["findings"] == []
    assert all(section["rows"] == 0 for section in report["sections"].values())
    with pytest.raises(ValueError, match="separate"):
        run(path, path)
    monkeypatch.setattr(
        "sys.argv", ["batch_integrity_doctor", "--database", str(path), "--output", str(output)]
    )
    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code == 0
    assert json.loads(output.read_text()) == report
    assert path.read_bytes() == before


def test_same_generation_legacy_request_envelope_remains_a_known_positive(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        owner(repo)
        save_active(repo)
    with closing(sqlite3.connect(path)) as connection:
        request = json.loads(
            connection.execute("SELECT request_json FROM llm_batch_intents").fetchone()[0]
        )
        question = request.pop("question")
        request.update(call_kind=question["kind"], ctx=question["ctx"])
        connection.execute("UPDATE llm_batch_intents SET request_json=?", (json.dumps(request),))
        connection.commit()
    report = audit(path)
    section = report["sections"]["llm_batch_intents"]
    assert section["assessed_rows"] == section["same_generation_rows"] == 1
    assert section["stale_generation_rows"] == 0
    assert report["findings"] == []


def test_damaged_null_generation_null_target_rows_are_invalid_not_duplicates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_computation_result(retained_result("analysis"))
        repo.save_result_application(application())
        repo.save_result_application(application().model_copy(update={"generation": 1}))
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "UPDATE result_applications SET generation=NULL, application_json=json_set(application_json,'$.generation',NULL)"
        )
        connection.commit()
        assert connection.execute("SELECT id FROM result_applications ORDER BY id").fetchall() == [
            (1,),
            (2,),
        ]
    report = audit(path)
    section = report["sections"]["applications"]
    assert section["rows"] == section["invalid_rows"] == 2
    assert section["valid_rows"] == section["duplicate_groups"] == section["duplicate_rows"] == 0
    assert {row["code"] for row in report["findings"]} == {"invalid_application_identity"}

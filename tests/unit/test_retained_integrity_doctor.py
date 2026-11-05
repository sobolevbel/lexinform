import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
from tools.retained_integrity_doctor import audit, run

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AnalysisRecord,
    DeliveryPlan,
    DocumentWork,
    Editorial,
    Mnemonic,
    NameRecord,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
    ReviewWork,
    Stage,
)
from lexinform.models.legacy_results import canonical_legacy_json
from lexinform.name_binding import bind_mnemonic
from tests.computations import NOW, retained_result
from tests.harness import summary
from tests.unit.test_legacy_results import decoded, imported, saved_value


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "state.db"


def repository(path: Path) -> SqliteBillRepository:
    repo = SqliteBillRepository(path)
    repo.migrate()
    repo.upsert_summary(summary("3039", "Technical retained owner"), now=NOW)
    return repo


def sql(path: Path, statement: str, parameters: tuple[object, ...] = ()) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(statement, parameters)


def body_archive(path: Path) -> str:
    value = saved_value()
    with closing(repository(path)) as repo:
        repo.import_legacy_results(imported(value))
    envelope = decoded(value)
    body = canonical_legacy_json(envelope.pop("analysis"))
    body_sha = hashlib.sha256(body.encode()).hexdigest()
    sql(path, "INSERT INTO legacy_analysis_bodies VALUES (?,?)", (body_sha, body))
    sql(
        path,
        "UPDATE legacy_result_values SET envelope_json=?,analysis_body_sha256=? WHERE value_id=?",
        (canonical_legacy_json(envelope), body_sha, value.value_id),
    )
    return value.value_id


@pytest.mark.parametrize("fault", ["body_corrupt", "body_missing", "value_corrupt", "manifest"])
def test_known_imported_body_integrity_is_censused(path: Path, fault: str) -> None:
    body_archive(path)
    statements = {
        "body_corrupt": "UPDATE legacy_analysis_bodies SET body_json='{}'",
        "body_missing": "DELETE FROM legacy_analysis_bodies",
        "value_corrupt": "UPDATE legacy_result_values SET envelope_json='{}'",
        "manifest": "UPDATE legacy_result_imports SET manifest_sha256='bad'",
    }
    sql(path, statements[fault])
    report = audit(path)
    assert report["summary"]["errors"] > 0
    assert report["sections"]["legacy_archive"]["value_rows"] == 1


def test_valid_shared_original_is_not_upcast_or_rewritten(path: Path) -> None:
    identifier = body_archive(path)
    with closing(SqliteBillRepository(path)) as repo:
        bill = repo.get(10, "3039")
        assert bill is not None, "the imported fixture has a current owner"
        repo.create_publication(
            Publication(
                term=10,
                number="3039",
                kind=PublicationKind.NEW_BILL,
                status=PublicationStatus.SENT,
                channel_id="cleanup",
                created_at=NOW,
                sent_at=NOW,
                message_id=1,
                delivery=DeliveryPlan(
                    bill_json=bill.model_dump_json(),
                    rendered_text="Saved",
                    policy_version="attention-v1",
                ),
            )
        )
        removed = repo.prune_history(
            before=NOW + timedelta(days=1), memo_before=NOW + timedelta(days=1)
        )
        assert removed["delivery_payloads"] == 1
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    report = audit(path)
    assert report["summary"] == {"errors": 0, "warnings": 0}
    with closing(SqliteBillRepository(path)) as repo:
        value = repo.get_legacy_result(identifier)
        assert value is not None, "public import retains the original value"
        assert value == saved_value()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def publication(
    repo: SqliteBillRepository,
    *,
    parent: int | None = None,
    kind: PublicationKind = PublicationKind.NEW_BILL,
    legacy: bool = False,
) -> int:
    bill = repo.get(10, "3039")
    assert bill is not None, "fixture saves its owner before freezing a body"
    return repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=kind,
            status=PublicationStatus.UNKNOWN,
            channel_id="channel",
            ref=str(parent),
            created_at=NOW,
            message_id=777,
            delivery=DeliveryPlan(
                bill_json=bill.model_dump_json(),
                rendered_text="Frozen visible text",
                policy_version="legacy" if legacy else "attention-v1",
                continuation_parent=parent,
                reply_to=777,
            ),
        )
    )


@pytest.mark.parametrize("slot", ["delivery", "document", "review", "accounting"])
def test_native_retained_body_corruption_cannot_hide_in_valid_json(path: Path, slot: str) -> None:
    with closing(repository(path)) as repo:
        bill = repo.get(10, "3039")
        assert bill is not None, "fixture has a typed owner"
        if slot == "delivery":
            publication(repo)
        elif slot == "document":
            repo.save_document_work(
                DocumentWork(
                    key="amendments/1",
                    term=10,
                    number="3039",
                    change_id=1,
                    bill_json=bill.model_dump_json(),
                    detail_json=ProcessDetail(
                        **summary("3039", "Technical retained owner").model_dump()
                    ).model_dump_json(),
                    stage_json=Stage(
                        stage_type="CommitteeWork", stage_name="Committee work"
                    ).model_dump_json(),
                    created_at=NOW,
                    next_at=NOW,
                )
            )
        elif slot == "review":
            repo.save_review_work(
                10,
                "3039",
                (
                    ReviewWork(
                        memo_key="review",
                        purpose="consultation_review",
                        since=NOW,
                        baseline_day=NOW.date(),
                        input_bill_json=bill.model_dump_json(),
                    ),
                ),
            )
    if slot == "delivery":
        sql(
            path, "UPDATE publications SET delivery_json=json_set(delivery_json,'$.bill_json','{}')"
        )
    elif slot == "document":
        sql(path, "UPDATE document_work SET work_json=json_set(work_json,'$.bill_json','{}')")
    elif slot == "review":
        sql(
            path,
            "UPDATE bills SET review_work_json=json_set(review_work_json,'$[0].input_bill_json','{}')",
        )
    else:
        from_item = {
            "batch_id": "batch",
            "custom_id": "item",
            "call_kind": "name",
            "term": 10,
            "number": "3039",
            "meta_json": "{}",
            "result_json": "{}",
        }
        sql(
            path,
            "INSERT INTO llm_batches(batch_id,provider,call_kind,submitted_at,status,request_count,estimated_cost_usd) VALUES ('batch','openai','name',?,'completed',1,0)",
            (NOW.isoformat(),),
        )
        sql(
            path,
            "INSERT INTO llm_batch_items(batch_id,custom_id,call_kind,term,number,meta_json,result_json) VALUES (?,?,?,?,?,?,?)",
            tuple(from_item.values()),
        )
    report = audit(path)
    assert report["summary"]["errors"] > 0
    assert any(row["code"] == "invalid_retained_body" for row in report["findings"])


def selected_name(
    repo: SqliteBillRepository, *, primary: bool = False, foreign: bool = False
) -> None:
    result = retained_result("analysis" if primary else "name")
    record = result.record
    assert isinstance(record, (AnalysisRecord, NameRecord)), (
        "fixture selects an admitted paid source"
    )
    if isinstance(record, AnalysisRecord):
        record = record.model_copy(
            update={
                "analysis": record.analysis.model_copy(
                    update={"mnemonic_candidate": "Dobre imię", "mnemonic_basis": "Known basis"}
                )
            }
        )
        result = result.model_copy(update={"record": record})
        candidate = Mnemonic(
            name="Dobre imię",
            language=record.analysis.mnemonic_language,
            basis="Known basis",
            selected_at=NOW,
            origin="analysis",
        )
    else:
        candidate = Mnemonic(
            name=record.proposal.name,
            language="ru",
            basis=record.proposal.basis,
            selected_at=NOW,
            origin="backfill",
        )
    if foreign:
        result = result.model_copy(update={"number": "paid-owner"})
    repo.save_computation_result(result)
    name = bind_mnemonic(candidate, record, result)
    assert name.computation_id is not None, "actual shared binder certifies the candidate"
    repo.save_editorial(10, "3039", Editorial(mnemonic=name))


@pytest.mark.parametrize("fault", ["missing", "sha", "answer", "failed_unknown_sha"])
def test_known_current_name_paid_ref_is_checked(path: Path, fault: str) -> None:
    with closing(repository(path)) as repo:
        selected_name(repo)
    if fault == "missing":
        sql(path, "DELETE FROM computation_results")
    elif fault == "sha":
        sql(
            path,
            "UPDATE bills SET editorial_json=json_set(editorial_json,'$.mnemonic.request_sha256',?)",
            ("b" * 64,),
        )
    elif fault == "failed_unknown_sha":
        sql(
            path,
            "UPDATE bills SET editorial_json=json_remove(editorial_json,'$.mnemonic.request_sha256')",
        )
        sql(
            path,
            "UPDATE computation_results SET result_json=json_set(result_json,'$.validation','error','$.record',NULL)",
        )
    else:
        sql(
            path,
            "UPDATE bills SET editorial_json=json_set(editorial_json,'$.mnemonic.name','Changed without a new source')",
        )
    report = audit(path)
    assert any(row["code"] == "invalid_editorial_paid_ref" for row in report["findings"])


@pytest.mark.parametrize("case", ["analysis", "foreign", "manual", "legacy_hash_unknown"])
def test_permitted_name_provenance_is_not_relabelled_name_only(path: Path, case: str) -> None:
    with closing(repository(path)) as repo:
        if case == "manual":
            repo.save_editorial(
                10,
                "3039",
                Editorial(
                    mnemonic=Mnemonic(
                        name="Manual name",
                        language="ru",
                        basis="Operator",
                        origin="operator",
                        selected_at=NOW,
                    )
                ),
            )
        else:
            selected_name(repo, primary=case == "analysis", foreign=case == "foreign")
    if case == "legacy_hash_unknown":
        sql(
            path,
            "UPDATE bills SET editorial_json=json_remove(editorial_json,'$.mnemonic.request_sha256')",
        )
    report = audit(path)
    assert report["summary"]["errors"] == 0
    if case == "legacy_hash_unknown":
        assert report["sections"]["editorial_refs"]["unknown_rows"] == 1


@pytest.mark.parametrize("missing", [False, True])
def test_continuation_parent_is_a_local_physical_id_and_absence_is_advisory(
    path: Path, missing: bool
) -> None:
    with closing(repository(path)) as repo:
        parent = publication(repo)
        child = publication(
            repo, parent=777 if missing else parent, kind=PublicationKind.MESSAGE_PART
        )
        assert child != parent, "public outbox assigns distinct physical rows"
        if missing:
            repo.mark_publication(
                child, PublicationStatus.SENT, message_id=777, count_attempt=False
            )
    report = audit(path)
    assert report["summary"]["errors"] == 0
    inventory = report["sections"]["continuation_parents"]
    assert inventory["missing_rows"] == int(missing)
    assert inventory["present_rows"] == int(not missing)
    if missing:
        assert any(
            row["code"] == "continuation_parent_unavailable" and row["level"] == "warning"
            for row in report["findings"]
        )


@pytest.mark.parametrize("cleaned", [True, False])
def test_cleaned_payload_and_empty_legacy_owners_remain_unavailable(
    path: Path, cleaned: bool
) -> None:
    with closing(repository(path)) as repo:
        publication(repo, legacy=True)
        repo.save_review_work(
            10,
            "3039",
            (
                ReviewWork(
                    memo_key="old",
                    purpose="consultation_review",
                    since=NOW,
                    baseline_day=NOW.date(),
                ),
            ),
        )
    sql(
        path,
        "UPDATE publications SET delivery_json=NULL"
        if cleaned
        else "UPDATE publications SET delivery_json=json_set(delivery_json,'$.bill_json','')",
    )
    report = audit(path)
    assert report["summary"]["errors"] == 0
    assert report["sections"]["saved_bodies"]["absent_rows"] > 0


@pytest.mark.parametrize(
    "table", ["legacy_analysis_bodies", "publications", "bills", "llm_batch_items"]
)
def test_missing_schema_never_claims_empty_coverage(path: Path, table: str) -> None:
    with closing(repository(path)):
        pass
    sql(path, f"DROP TABLE {table}")
    report = audit(path)
    assert report["status"] == "partial"
    assert any(section["status"] == "unavailable" for section in report["sections"].values())


def test_report_is_bounded_separate_and_source_is_unchanged(path: Path, tmp_path: Path) -> None:
    with closing(repository(path)) as repo:
        publication(repo)
    sql(
        path,
        "UPDATE publications SET delivery_json=?",
        ("[" * 2000 + "SECRET-SOURCE" + "]" * 2000,),
    )
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    output = tmp_path / "audit.json"
    assert run(path, output) == 1
    assert "SECRET-SOURCE" not in output.read_text()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with pytest.raises(ValueError):
        run(path, path)


@pytest.mark.parametrize("profile", ["legacy", "future-policy"])
def test_unproven_body_profile_is_unavailable_not_native_corruption(
    path: Path, profile: str
) -> None:
    with closing(repository(path)) as repo:
        publication(repo, legacy=True)
    sql(
        path,
        "UPDATE publications SET delivery_json=json_set(delivery_json,'$.bill_json','{}','$.policy_version',?)",
        (profile,),
    )
    report = audit(path)
    assert report["summary"] == {"errors": 0, "warnings": 1}
    assert report["sections"]["saved_bodies"]["unknown_rows"] == 1
    assert report["findings"][0]["code"] == "legacy_body_unavailable"


@pytest.mark.parametrize("profile", ["attention-v1", "legacy", "future-policy", None])
def test_outer_model_failure_requires_explicit_native_profile(
    path: Path, profile: str | None
) -> None:
    with closing(repository(path)) as repo:
        publication(repo)
    sql(
        path,
        "UPDATE publications SET delivery_json=json_set(delivery_json,'$.reply_to',json('{\"invalid\":true}'))",
    )
    if profile is None:
        sql(
            path,
            "UPDATE publications SET delivery_json=json_remove(delivery_json,'$.policy_version')",
        )
    else:
        sql(
            path,
            "UPDATE publications SET delivery_json=json_set(delivery_json,'$.policy_version',?)",
            (profile,),
        )
    report = audit(path)
    native = profile == "attention-v1"
    assert report["summary"] == {"errors": int(native), "warnings": int(not native)}
    assert report["sections"]["continuation_parents"]["unavailable_rows"] == 1
    assert report["findings"][0]["code"] == (
        "invalid_retained_body" if native else "delivery_profile_unavailable"
    )


@pytest.mark.parametrize("fault", ["json", "reference"])
def test_legacy_body_storage_or_recognized_parent_failure_is_not_advisory(
    path: Path, fault: str
) -> None:
    with closing(repository(path)) as repo:
        publication(repo, legacy=True)
    body = (
        "{" if fault == "json" else json.dumps({"legacy_result_ref": "legacy-value-v1:" + "0" * 64})
    )
    sql(
        path,
        "UPDATE publications SET delivery_json=json_set(delivery_json,'$.bill_json',?)",
        (body,),
    )
    report = audit(path)
    assert report["summary"]["errors"] > 0
    assert any(row["code"] == "invalid_retained_body" for row in report["findings"])


def test_native_missing_required_body_uses_one_slot(path: Path) -> None:
    with closing(repository(path)) as repo:
        publication(repo)
    sql(path, "UPDATE publications SET delivery_json=json_set(delivery_json,'$.bill_json','')")
    report = audit(path)
    counts = report["sections"]["saved_bodies"]
    assert report["summary"]["errors"] == 1
    assert counts["slots"] == sum(
        counts[key] for key in ("valid_rows", "invalid_rows", "unknown_rows", "absent_rows")
    )
    assert any(row["code"] == "missing_retained_body" for row in report["findings"])


@pytest.mark.parametrize("case", ["valid_analysis", "valid_foreign_name", "missing_name_parent"])
def test_public_rename_preserves_known_previous_name_dependency(path: Path, case: str) -> None:
    with closing(repository(path)) as repo:
        selected_name(repo, primary=case == "valid_analysis", foreign=case == "valid_foreign_name")
        bill = repo.get(10, "3039")
        assert bill is not None, "the public fixture saves its current owner"
        manual = Mnemonic(
            name="Manual current",
            language="ru",
            basis="Operator rename",
            origin="operator",
            selected_at=NOW,
        )
        repo.save_editorial(10, "3039", bill.editorial.rename(manual))
        saved = repo.get(10, "3039")
        assert saved is not None and saved.editorial.mnemonic is not None, (
            "public rename retains a selected current name"
        )
        assert saved.editorial.mnemonic.computation_id is None
        assert len(saved.editorial.previous) == 1
        assert saved.editorial.previous[0].computation_id is not None, (
            "rename preserves the paid historical source"
        )
    if case == "missing_name_parent":
        sql(path, "DELETE FROM computation_results")
    report = audit(path)
    references = report["sections"]["editorial_refs"]
    if case == "missing_name_parent":
        assert any(
            row["code"] == "invalid_editorial_paid_ref" and row["ref"].endswith("/previous/0")
            for row in report["findings"]
        )
    assert references["known_rows"] == 0
    assert references["previous_rows"] == 1
    assert references["previous_known_rows"] == 1
    if case == "missing_name_parent":
        assert references["previous_invalid_rows"] == 1
        assert any(
            row["code"] == "invalid_editorial_paid_ref" and row["ref"].endswith("/previous/0")
            for row in report["findings"]
        )
    else:
        assert references["previous_valid_rows"] == 1
        assert report["summary"]["errors"] == 0

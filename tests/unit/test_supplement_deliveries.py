import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any, Never

import pytest
from tools.computation_doctor import audit, main

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.supplement_deliveries import supplement_delivery_links
from lexinform.models import (
    ComputationResult,
    DeliveryPlan,
    DigestItemMeta,
    LlmBatch,
    LlmBatchItem,
    ProcessSummary,
    Publication,
    PublicationKind,
    PublicationStatus,
    ResultApplication,
    ResultApplicationTarget,
    StatusChange,
    SupplementInputReceipt,
    SupplementRecord,
)
from lexinform.models.prepared_input import PreparedTextPart
from tests.computations import NOW, retained_result

MARKER = "PRIVATE-CONTEXT-DO-NOT-EMIT"


def database(tmp_path: Path, process: ProcessSummary) -> tuple[Path, ComputationResult, int, int]:
    path = tmp_path / "state.db"
    result = retained_result("supplement")
    assert isinstance(result.record, SupplementRecord), "the fixture retains a validated supplement"
    projected = result.record.model_copy(
        update={
            "number": "extra-print",
            "title": MARKER,
            "source_url": "https://secret.test/" + MARKER,
        }
    )
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.upsert_summary(process, now=NOW)
        repo.save_computation_result(result)
        change = StatusChange(
            term=10,
            number="3039",
            old_fingerprint=None,
            new_fingerprint="supplement",
            new_stages=[],
            supplements=[projected],
            detected_at=NOW,
        )
        identity = repo.add_status_change(change)
        assert identity is not None, "the fixture inserts its first unique change"
        repo.save_result_application(
            ResultApplication(
                result_id=result.result_id,
                term=10,
                number="3039",
                generation=None,
                target=ResultApplicationTarget(kind="status_change", ref=str(identity)),
                phase="projection",
                outcome="applied",
                reason="retained_result",
                observed_generation=None,
                observed_status=None,
                created_at=NOW,
            )
        )
        pub_id = repo.create_publication(
            Publication(
                term=10,
                number="3039",
                kind=PublicationKind.STATUS_UPDATE,
                status=PublicationStatus.SENT,
                channel_id="channel",
                status_change_id=identity,
                created_at=NOW,
                sent_at=NOW,
                message_id=152,
                delivery=DeliveryPlan(
                    bill_json=json.dumps({"private": MARKER}),
                    change_json=change.model_copy(update={"id": identity}).model_dump_json(),
                    rendered_text=MARKER,
                ),
            )
        )
    return path, result, identity, pub_id


def inspect(path: Path) -> dict[str, Any]:
    before = path.read_bytes()
    with closing(sqlite3.connect(path)) as connection:
        report = supplement_delivery_links(connection)
        assert connection.row_factory is None and not connection.total_changes
    assert path.read_bytes() == before
    assert MARKER not in json.dumps(report) and "https://" not in json.dumps(report)
    return report


def test_saved_payload_proves_only_recorded_sent_without_current_bill_or_source_inference(
    tmp_path: Path, process_3039: ProcessSummary
) -> None:
    path, result, identity, pub_id = database(tmp_path, process_3039)
    report = inspect(path)
    row = report["links"][0]
    assert row["computation_id"] == result.result_id and row["change_id"] == identity
    assert row["publication_id"] == pub_id and row["delivery"] == "recorded_sent"
    assert row["batch"]["status"] == "unavailable" and row["source_edge"] == "not_assessed"
    assert row["input_receipt"] == "unknown"


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("request", "frozen_request_mismatch"),
        ("change_id", "frozen_change_mismatch"),
        ("record_id", "frozen_reference_mismatch"),
        ("missing_payload", "payload_unavailable"),
        ("group", "compound_delivery_unassessed"),
        ("malformed", "payload_invalid"),
    ],
)
def test_unverified_payloads_stay_unknown_without_reinterpreting_history(
    tmp_path: Path, process_3039: ProcessSummary, fault: str, reason: str
) -> None:
    path, _, _, pub_id = database(tmp_path, process_3039)
    with closing(sqlite3.connect(path)) as db, db:
        raw = db.execute("SELECT delivery_json FROM publications WHERE id=?", (pub_id,)).fetchone()[
            0
        ]
        plan = json.loads(raw)
        change = json.loads(plan["change_json"])
        if fault == "request":
            change["supplements"][0]["request_sha256"] = "b" * 64
        elif fault == "change_id":
            change["id"] += 1
        elif fault == "record_id":
            change["supplements"][0]["computation_id"] = "result-v1:" + "f" * 64
        elif fault == "group":
            plan["sitting_group"] = [1, 2]
        plan["change_json"] = json.dumps(change)
        db.execute(
            "UPDATE publications SET delivery_json=? WHERE id=?",
            (
                None
                if fault == "missing_payload"
                else "bad"
                if fault == "malformed"
                else json.dumps(plan),
                pub_id,
            ),
        )
    row = inspect(path)["links"][0]
    assert row["delivery"] == "unknown" and row["reason"] == reason


def test_later_status_change_contents_do_not_replace_original_frozen_context(
    tmp_path: Path, process_3039: ProcessSummary
) -> None:
    path, _, identity, _ = database(tmp_path, process_3039)
    with closing(SqliteBillRepository(path)) as repo:
        repo.save_status_change_supplements(identity, [])
    assert inspect(path)["links"][0]["delivery"] == "recorded_sent"


@pytest.mark.parametrize("status", ["unknown", "failed", "queued", "pending", "dismissed"])
def test_uncertain_or_unconfirmed_physical_rows_never_become_sent(
    tmp_path: Path, process_3039: ProcessSummary, status: str
) -> None:
    path, _, _, pub_id = database(tmp_path, process_3039)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE publications SET status=? WHERE id=?", (status, pub_id))
    row = inspect(path)["links"][0]
    assert row["delivery"] == "not_confirmed" and row["physical_status"] == status


def test_diagnostic_is_opt_in_and_preserves_the_default_report(
    tmp_path: Path, process_3039: ProcessSummary
) -> None:
    path, _, _, _ = database(tmp_path, process_3039)
    ordinary = audit(path)
    expanded = audit(path, include_supplement_deliveries=True)
    assert expanded.pop("supplement_deliveries") == inspect(path)
    assert expanded == ordinary


@pytest.mark.parametrize(
    "fault", ["matched", "request", "unconsumed", "receipt", "bad_consumption"]
)
def test_consumed_batch_evidence_and_receipt_availability_remain_separate(
    tmp_path: Path, process_3039: ProcessSummary, fault: str
) -> None:
    path, result, _, _ = database(tmp_path, process_3039)
    assert result.request_sha256 is not None, "the retained fixture has a known frozen checksum"
    receipt = (
        SupplementInputReceipt(
            context_sha256="c" * 64,
            request_sha256=result.request_sha256,
            text_sha256="d" * 64,
            text_chars=1,
            source_part=PreparedTextPart(text_sha256="e" * 64, chars=1, role="bill"),
            retention="unknown",
            reason="projection_unavailable",
        )
        if fault == "receipt"
        else None
    )
    with closing(SqliteBillRepository(path)) as repo:
        repo.save_llm_batch(
            LlmBatch(
                batch_id=result.batch_id,
                provider=result.provider,
                call_kind="supplement",
                submitted_at=NOW,
                status="ended",
                request_count=1,
                estimated_cost_usd=0,
            ),
            [
                LlmBatchItem(
                    batch_id=result.batch_id,
                    custom_id=result.custom_id,
                    call_kind="supplement",
                    term=result.term,
                    number=result.number,
                    meta=DigestItemMeta(
                        memo_key=result.custom_id,
                        request_sha256="b" * 64 if fault == "request" else result.request_sha256,
                        prepared_input_receipt=receipt,
                    ),
                )
            ],
        )
        if fault != "unconsumed":
            repo.mark_llm_batch_item_consumed(result.batch_id, result.custom_id, consumed_at=NOW)
    if fault == "bad_consumption":
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE llm_batch_items SET consumed_at=?", (MARKER,))
    row = inspect(path)["links"][0]
    assert row["batch"]["status"] == (
        "invalid"
        if fault == "bad_consumption"
        else "mismatch"
        if fault == "request"
        else "unconsumed"
        if fault == "unconsumed"
        else "consumed"
    )
    assert row["input_receipt"] == ("present" if fault == "receipt" else "unknown")
    assert row["source_edge"] == "not_assessed" and row["delivery"] == "recorded_sent"


def test_missing_schema_is_unavailable_instead_of_an_empty_accepted_population() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        report = supplement_delivery_links(connection)
    assert report["status"] == "unavailable" and report["unavailable_schema"]


@pytest.mark.parametrize("fault", ["result_columns", "application_columns"])
def test_corrupt_indexed_identities_are_unclassified_not_positive_delivery_proof(
    tmp_path: Path, process_3039: ProcessSummary, fault: str
) -> None:
    path, _, _, _ = database(tmp_path, process_3039)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "UPDATE "
            + ("computation_results" if fault == "result_columns" else "result_applications")
            + " SET number='different'"
        )
    report = inspect(path)
    assert report["unclassified_applications"] == 1 and not report["links"]


def test_command_line_flag_exposes_only_the_opt_in_section(
    tmp_path: Path, process_3039: ProcessSummary, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _, _, _ = database(tmp_path, process_3039)
    output = tmp_path / "report.json"
    before = path.read_bytes()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "computation-doctor",
            "--database",
            str(path),
            "--output",
            str(output),
            "--supplement-deliveries",
        ],
    )
    with pytest.raises(SystemExit):
        main()
    report = json.loads(output.read_text())
    assert report["supplement_deliveries"] == inspect(path)
    assert path.read_bytes() == before


def test_sent_without_a_saved_message_id_remains_unknown(
    tmp_path: Path, process_3039: ProcessSummary
) -> None:
    path, _, _, pub_id = database(tmp_path, process_3039)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE publications SET message_id=NULL WHERE id=?", (pub_id,))
    row = inspect(path)["links"][0]
    assert row["delivery"] == "unknown" and row["reason"] == "message_id_unavailable"


def test_failed_retained_result_cannot_support_a_positive_delivery_reference(
    tmp_path: Path, process_3039: ProcessSummary
) -> None:
    path, result, _, _ = database(tmp_path, process_3039)
    value = result.model_dump(mode="json")
    value.update(validation="error", record=None, error=MARKER)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE computation_results SET result_json=?", (json.dumps(value),))
    row = inspect(path)["links"][0]
    assert row["delivery"] == "unknown" and row["reason"] == "retained_result_unvalidated"


def test_cancellation_is_not_reclassified_as_unknown(
    tmp_path: Path, process_3039: ProcessSummary, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _, _, _ = database(tmp_path, process_3039)
    before = path.read_bytes()
    error = KeyboardInterrupt("cancel")

    def cancel(*args: object, **kwargs: object) -> Never:
        raise error

    monkeypatch.setattr(DeliveryPlan, "model_validate", cancel)
    with closing(sqlite3.connect(path)) as connection, pytest.raises(KeyboardInterrupt) as caught:
        supplement_delivery_links(connection)
    assert caught.value is error and path.read_bytes() == before


@pytest.mark.parametrize(
    "flag,value",
    [
        ("destination", "digest_only"),
        ("absorbed_by", 300),
        ("digest_event_ids", [12]),
        ("digest_parts", [12]),
        ("digest_ref", "2026-W44"),
    ],
)
def test_digest_or_absorbed_event_sources_are_not_individual_sent_deliveries(
    tmp_path: Path, process_3039: ProcessSummary, flag: str, value: Any
) -> None:
    path, _, _, pub_id = database(tmp_path, process_3039)
    with closing(sqlite3.connect(path)) as db, db:
        plan = json.loads(
            db.execute("SELECT delivery_json FROM publications WHERE id=?", (pub_id,)).fetchone()[0]
        )
        plan[flag] = value
        db.execute("UPDATE publications SET delivery_json=? WHERE id=?", (json.dumps(plan), pub_id))
    row = inspect(path)["links"][0]
    assert row["delivery"] == "unknown" and row["reason"] == "compound_delivery_unassessed"


def test_explicit_digest_ownership_keeps_an_apparently_direct_source_unassessed(
    tmp_path: Path, process_3039: ProcessSummary
) -> None:
    path, _, _, pub_id = database(tmp_path, process_3039)
    with closing(sqlite3.connect(path)) as db, db:
        raw = db.execute("SELECT delivery_json FROM publications WHERE id=?", (pub_id,)).fetchone()[
            0
        ]
        plan = json.loads(raw)
        plan["destination"] = "digest_only"
        db.execute(
            "UPDATE publications SET status='queued', delivery_json=? WHERE id=?",
            (json.dumps(plan), pub_id),
        )
    with closing(SqliteBillRepository(path)) as repo:
        owner = repo.create_publication(
            Publication(
                term=0,
                number="digest",
                kind=PublicationKind.DIGEST,
                status=PublicationStatus.QUEUED,
                channel_id="channel",
                ref="2026-W44",
                created_at=NOW,
                delivery=DeliveryPlan(
                    bill_json="{}", digest_event_ids=(pub_id,), rendered_text=MARKER
                ),
            )
        )
        repo.reserve_digest_events(owner, (pub_id,))
        repo.complete_digest_events(owner, message_id=300, sent_at=NOW)
        source = repo.publication_by_id(pub_id)
        assert source is not None and source.status is PublicationStatus.SENT, (
            "digest completion marks the event sent without sending its standalone payload"
        )
    with closing(sqlite3.connect(path)) as db, db:
        plan.pop("destination")
        db.execute("UPDATE publications SET delivery_json=? WHERE id=?", (json.dumps(plan), pub_id))
    row = inspect(path)["links"][0]
    assert row["physical_status"] == "sent" and row["delivery"] == "unknown"
    assert row["reason"] == "compound_delivery_unassessed"


@pytest.mark.parametrize("boundary", ["application", "plan", "change"])
def test_duplicate_keys_never_certify_an_ambiguous_saved_identity_or_payload(
    tmp_path: Path, process_3039: ProcessSummary, boundary: str
) -> None:
    path, _, _, pub_id = database(tmp_path, process_3039)
    with closing(sqlite3.connect(path)) as db, db:
        if boundary == "application":
            raw = db.execute("SELECT application_json FROM result_applications").fetchone()[0]
            db.execute(
                "UPDATE result_applications SET application_json=?",
                ('{"result_id":"ambiguous",' + raw[1:],),
            )
        else:
            raw = db.execute(
                "SELECT delivery_json FROM publications WHERE id=?", (pub_id,)
            ).fetchone()[0]
            if boundary == "plan":
                raw = '{"change_json":"ambiguous",' + raw[1:]
            else:
                plan = json.loads(raw)
                plan["change_json"] = plan["change_json"].replace(
                    '"computation_id":', '"computation_id":"ambiguous","computation_id":', 1
                )
                raw = json.dumps(plan)
            db.execute("UPDATE publications SET delivery_json=? WHERE id=?", (raw, pub_id))
    report = inspect(path)
    if boundary == "application":
        assert report["unclassified_applications"] == 1 and not report["links"]
    else:
        assert report["links"][0]["delivery"] == "unknown"
        assert report["links"][0]["reason"] == "payload_invalid"

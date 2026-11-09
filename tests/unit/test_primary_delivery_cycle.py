import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
from tools.audit_saved_primary_claims import ReviewInputCheckpoint
from tools.primary_delivery_cycle import audit, census, main

from lexinform.models import (
    AmendmentsRecord,
    BatchResult,
    ComputationInputBinding,
    ComputationResult,
    DeliveryPlan,
    PreparedTextPart,
    Publication,
    PublicationKind,
    PublicationStatus,
    ResultApplication,
    ResultApplicationTarget,
    StatusChange,
    SupplementRecord,
    TokenUsage,
    computation_id,
)
from lexinform.models.input_receipts import (
    AmendmentInputReceipt,
    SupplementInputReceipt,
)
from tests.fakes import make_amendments, make_digest
from tests.unit.test_content_sources import Case
from tests.unit.test_content_sources import case as case
from tests.unit.test_input_receipts import NOW


def card_snapshot(
    case: Case, target: Path, *, kind: PublicationKind = PublicationKind.NEW_BILL
) -> int:
    generation = case.paid.generation
    assert generation is not None, "the fixture has a paid primary generation"
    projection = case.repo.list_result_applications(10, "3039")[0]
    case.repo.save_result_application(
        projection.model_copy(update={"generation": generation, "projected_generation": 8})
    )
    case.repo.save_result_application(
        projection.model_copy(
            update={
                "generation": generation,
                "phase": "collection",
                "reason": "current_generation",
                "outcome": "ready",
            }
        )
    )
    case.execute(
        "UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.source_url',?)"
        " WHERE batch_id=? AND custom_id=?",
        (case.observation.document.url, case.item.batch_id, case.item.custom_id),
    )
    bill = case.bill.model_copy(update={"analysis_generation": 8})
    identity = case.repo.create_publication(
        Publication(
            term=bill.term,
            number=bill.number,
            kind=kind,
            status=PublicationStatus.SENT,
            channel_id="SECRET-CHANNEL",
            message_id=5216,
            created_at=NOW,
            sent_at=NOW,
            delivery=DeliveryPlan(
                bill_json=bill.model_dump_json(),
                rendered_text="DO-NOT-EMIT-FROZEN-CARD",
            ),
        )
    )
    with (
        closing(sqlite3.connect(case.path)) as source,
        closing(sqlite3.connect(target)) as snapshot,
    ):
        source.backup(snapshot)
    return identity


def test_primary_cycle_uses_frozen_card_and_exact_saved_receipts(
    case: Case, tmp_path: Path
) -> None:
    snapshot = tmp_path / "checkpoint.db"
    identity = card_snapshot(case, snapshot)
    before = snapshot.read_bytes()

    report = audit(snapshot, identity)

    assert report["status"] == "recorded"
    cycle = report["cycle"]
    assert isinstance(cycle, dict), "a recorded report carries the bounded cycle identities"
    assert cycle["source_observation_id"] == case.observation.observation_id
    assert cycle["source_selection_id"] == case.selected.selection_id
    assert cycle["result_id"] == case.paid.result_id
    assert cycle["request_sha256"] == case.paid.request_sha256
    assert cycle["collection_application_id"] != cycle["projection_application_id"]
    encoded = json.dumps(report)
    assert all(
        value not in encoded
        for value in ("DO-NOT-EMIT-FROZEN-CARD", "SECRET-CHANNEL", "message_id")
    )
    assert snapshot.read_bytes() == before


def test_expired_plan_can_report_the_same_send_from_a_pinned_snapshot(
    case: Case, tmp_path: Path
) -> None:
    earlier = tmp_path / "earlier.db"
    current = tmp_path / "current.db"
    identity = card_snapshot(case, earlier)
    earlier_bytes = earlier.read_bytes()
    current.write_bytes(earlier_bytes)
    with closing(sqlite3.connect(current)) as db, db:
        db.execute("UPDATE publications SET delivery_json=NULL")
    current_bytes = current.read_bytes()
    pin = hashlib.sha256(earlier_bytes).hexdigest()

    report = audit(current, identity, delivery_checkpoint=ReviewInputCheckpoint(earlier, pin))

    assert report.pop("historical_delivery_checkpoint") == {
        "status": "recorded",
        "reason": "exact_retained_cycle",
        "checkpoint_sha256": pin,
        "cycle": audit(earlier, identity)["cycle"],
    }
    assert report == audit(current, identity)
    assert report["reason"] == "frozen_plan_unavailable"
    assert earlier.read_bytes() == earlier_bytes
    assert current.read_bytes() == current_bytes


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("UPDATE publications SET message_id=message_id+1", "recorded physical send"),
        (
            "INSERT INTO publications (term, number, kind, status, channel_id, message_id, created_at)"
            " SELECT term, 'other', kind, status, channel_id, message_id, created_at FROM publications",
            "Current physical send is ambiguous",
        ),
    ],
)
def test_expired_plan_rejects_checkpoint_for_unproven_send(
    case: Case, tmp_path: Path, mutation: str, message: str
) -> None:
    earlier = tmp_path / "earlier.db"
    current = tmp_path / "current.db"
    identity = card_snapshot(case, earlier)
    earlier_bytes = earlier.read_bytes()
    current.write_bytes(earlier_bytes)
    with closing(sqlite3.connect(current)) as db, db:
        db.execute("UPDATE publications SET delivery_json=NULL")
        db.execute(mutation)

    with pytest.raises(ValueError, match=message):
        audit(
            current,
            identity,
            delivery_checkpoint=ReviewInputCheckpoint(
                earlier, hashlib.sha256(earlier_bytes).hexdigest()
            ),
        )


def test_absent_and_other_kind_do_not_reuse_the_paid_context(case: Case, tmp_path: Path) -> None:
    snapshot = tmp_path / "checkpoint.db"
    identity = card_snapshot(case, snapshot, kind=PublicationKind.AGENDA)

    other = audit(snapshot, identity)
    absent = audit(snapshot, identity + 1)

    assert other["status"] == absent["status"] == "not_observed"
    assert other["reason"] == "publication_outside_primary_card_scope"
    assert absent["reason"] == "publication_unavailable"
    assert other["cycle"] is None and absent["cycle"] is None


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("UPDATE publications SET status='unknown'", "recorded_send_unavailable"),
        ("UPDATE publications SET delivery_json=NULL", "frozen_plan_unavailable"),
        ("DELETE FROM source_observations", "unique_source_observation_unavailable"),
        (
            "UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.source_url',NULL)",
            "frozen_source_context_unavailable",
        ),
        (
            "DELETE FROM result_applications WHERE phase='collection'",
            "unique_collection_application_unavailable",
        ),
    ],
)
def test_lost_receipts_remain_unknown(
    case: Case, tmp_path: Path, mutation: str, reason: str
) -> None:
    snapshot = tmp_path / "checkpoint.db"
    identity = card_snapshot(case, snapshot)
    with closing(sqlite3.connect(snapshot)) as db, db:
        db.execute(mutation)

    report = audit(snapshot, identity)

    assert report["status"] == "not_observed"
    assert report["reason"] == reason
    assert report["cycle"] is None


def test_conflicting_chronology_is_invalid(case: Case, tmp_path: Path) -> None:
    snapshot = tmp_path / "checkpoint.db"
    identity = card_snapshot(case, snapshot)
    with closing(sqlite3.connect(snapshot)) as db, db:
        db.execute("UPDATE publications SET sent_at='2000-01-01T00:00:00+00:00'")

    report = audit(snapshot, identity)

    assert report["status"] == "invalid"
    assert report["cycle"] is None


@pytest.fixture(params=["amendments", "supplement"])
def secondary_snapshot(
    case: Case, tmp_path: Path, request: pytest.FixtureRequest
) -> tuple[Path, int, int]:
    purpose = "amendments" if request.param == "amendments" else "supplement"
    result_id = computation_id("openai", "secondary-batch", purpose)
    if purpose == "amendments":
        record: AmendmentsRecord | SupplementRecord = AmendmentsRecord(
            amendments=make_amendments(),
            source_kind="senate_amendments",
            model="frozen-model",
            prompt_version="frozen-prompt",
            source_url="https://example.test/private-source",
            created_at=NOW,
            computation_id=result_id,
            request_sha256="a" * 64,
        )
    else:
        record = SupplementRecord(
            number="3039-A",
            title="PRIVATE-TITLE",
            source_kind="print",
            digest=make_digest(),
            model="frozen-model",
            prompt_version="frozen-prompt",
            source_url="https://example.test/private-source",
            created_at=NOW,
            computation_id=result_id,
            request_sha256="a" * 64,
        )
    paid = ComputationResult(
        schema_version=2,
        provider="openai",
        batch_id="secondary-batch",
        custom_id=purpose,
        purpose=purpose,
        term=case.bill.term,
        number=case.bill.number,
        generation=None,
        collected_at=NOW,
        request_sha256="a" * 64,
        input_binding=ComputationInputBinding(
            term=case.bill.term, number=case.bill.number, reuse_key="fixture"
        ),
        validation="validated",
        record=record,
        model="frozen-model",
        usage=TokenUsage(),
    )
    case.repo.save_computation_result(paid)
    record = record.model_copy(update={"computation_id": paid.result_id, "input_tokens": 100})
    change = StatusChange(
        term=case.bill.term,
        number=case.bill.number,
        old_fingerprint=None,
        new_fingerprint="frozen",
        new_stages=[],
        detected_at=NOW,
        amendments=record if isinstance(record, AmendmentsRecord) else None,
        supplements=[record] if isinstance(record, SupplementRecord) else [],
    )
    change_id = case.repo.add_status_change(change)
    assert change_id is not None, "fixture inserts a fresh change"
    change.id = change_id
    case.repo.save_result_application(
        ResultApplication(
            result_id=paid.result_id,
            term=change.term,
            number=change.number,
            generation=None,
            target=ResultApplicationTarget(kind="status_change", ref=str(change_id)),
            phase="projection",
            outcome="applied",
            reason="retained_result",
            observed_generation=None,
            observed_status=None,
            created_at=NOW,
        )
    )
    identity = case.repo.create_publication(
        Publication(
            term=change.term,
            number=change.number,
            kind=PublicationKind.STATUS_UPDATE,
            status=PublicationStatus.SENT,
            channel_id="SECRET-CHANNEL",
            message_id=52,
            status_change_id=change_id,
            created_at=NOW,
            sent_at=NOW,
            delivery=DeliveryPlan(
                bill_json=case.bill.model_dump_json(),
                reply_to=10,
                change_json=change.model_dump_json(),
                rendered_text="SECRET-RENDER",
            ),
        )
    )
    target = tmp_path / "secondary-checkpoint.db"
    with (
        closing(sqlite3.connect(case.path)) as source,
        closing(sqlite3.connect(target)) as snapshot,
    ):
        source.backup(snapshot)
        application_id = snapshot.execute(
            "SELECT id FROM result_applications WHERE result_id=?", (paid.result_id,)
        ).fetchone()[0]
    return target, identity, int(application_id)


def test_secondary_suffix_only_claims_the_saved_projection_and_send(
    secondary_snapshot: tuple[Path, int, int],
) -> None:
    target, publication, application = secondary_snapshot
    before = target.read_bytes()

    report = audit(target, publication, secondary_application_id=application)

    assert report["status"] == "recorded"
    suffix = report["suffix"]
    assert isinstance(suffix, dict), "recorded suffix has its saved identifiers"
    assert suffix["application_id"] == application
    assert suffix["purpose"] in {"amendments", "supplement"}
    assert report["reason"] == "exact_retained_secondary_suffix"
    assert "cycle" not in report
    assert all(
        private not in json.dumps(report)
        for private in ("SECRET-RENDER", "SECRET-CHANNEL", "private-source", "PRIVATE-TITLE")
    )
    assert target.read_bytes() == before
    assert audit(target, publication)["reason"] == "publication_outside_primary_card_scope"


@pytest.mark.parametrize(
    ("mutation", "status", "reason"),
    [
        ("UPDATE publications SET status='unknown'", "not_observed", "recorded_send_unavailable"),
        ("UPDATE publications SET delivery_json=NULL", "not_observed", "frozen_plan_unavailable"),
        (
            "UPDATE result_applications SET application_json=json_set(application_json,"
            "'$.target.ref','999') WHERE json_type(application_json,'$.target')='object'",
            "not_observed",
            "application_outside_exact_status_target",
        ),
        (
            "UPDATE status_changes SET amendments_json=NULL,supplements_json=NULL",
            "not_observed",
            "unique_secondary_record_unavailable",
        ),
        (
            "UPDATE status_changes SET new_fingerprint='different'",
            "invalid",
            "invalid_snapshot_evidence",
        ),
    ],
)
def test_secondary_suffix_rejects_missing_or_conflicting_parents(
    secondary_snapshot: tuple[Path, int, int], mutation: str, status: str, reason: str
) -> None:
    target, publication, application = secondary_snapshot
    with closing(sqlite3.connect(target)) as db, db:
        db.execute(mutation)

    report = audit(target, publication, secondary_application_id=application)

    assert report["status"] == status
    assert report["reason"] == reason
    assert report["suffix"] is None


def add_secondary_batch(target: Path, *, include_receipt: bool = False) -> None:
    with closing(sqlite3.connect(target)) as db, db:
        paid = ComputationResult.model_validate_json(
            db.execute(
                "SELECT result_json FROM computation_results WHERE batch_id='secondary-batch'"
            ).fetchone()[0]
        )
        binding = paid.input_binding
        assert binding is not None, "secondary fixture retains the original binding"
        request_sha = paid.request_sha256
        assert request_sha is not None, "secondary fixture retains the request digest"
        metadata: dict[str, object] = {
            "input_binding": binding.model_dump(),
            "memo_key": binding.reuse_key,
            "request_sha256": request_sha,
            "queued_at": NOW.isoformat(),
        }
        if include_receipt:
            receipt_type = (
                AmendmentInputReceipt if paid.purpose == "amendments" else SupplementInputReceipt
            )
            metadata["prepared_input_receipt"] = receipt_type(
                context_sha256=request_sha,
                request_sha256=request_sha,
                text_sha256=request_sha,
                text_chars=1,
                source_part=PreparedTextPart(text_sha256=request_sha, chars=1, role="bill"),
                retention="unknown",
                reason="projection_unavailable",
            ).model_dump(mode="json")
        db.execute(
            "INSERT INTO llm_batches (batch_id, provider, call_kind, submitted_at, status,"
            " request_count, estimated_cost_usd) VALUES (?,?,?,?,?,?,?)",
            (paid.batch_id, paid.provider, paid.purpose, NOW.isoformat(), "ended", 1, 0),
        )
        db.execute(
            "INSERT INTO llm_batch_items (batch_id, custom_id, call_kind, term, number,"
            " meta_json, consumed_at) VALUES (?,?,?,?,?,?,?)",
            (
                paid.batch_id,
                paid.custom_id,
                paid.purpose,
                paid.term,
                paid.number,
                json.dumps(metadata),
                NOW.isoformat(),
            ),
        )


@pytest.mark.parametrize(
    "control", ["exact", "receipt", "missing", "request", "owner", "timestamp"]
)
def test_secondary_request_prefix_keeps_exact_suffix_and_rejects_prefix_conflicts(
    secondary_snapshot: tuple[Path, int, int], control: str
) -> None:
    target, publication, application = secondary_snapshot
    if control != "missing":
        add_secondary_batch(target, include_receipt=control == "receipt")
    mutations = {
        "request": "UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.request_sha256',?)"
        " WHERE batch_id='secondary-batch'",
        "owner": "UPDATE llm_batch_items SET number=? WHERE batch_id='secondary-batch'",
        "timestamp": "UPDATE llm_batches SET submitted_at=? WHERE batch_id='secondary-batch'",
    }
    if control in mutations:
        value = {"request": "b" * 64, "owner": "other", "timestamp": "2099-01-01T00:00:00Z"}[
            control
        ]
        with closing(sqlite3.connect(target)) as db, db:
            db.execute(mutations[control], (value,))
    before = target.read_bytes()

    ordinary = audit(target, publication, secondary_application_id=application)
    report = audit(
        target, publication, secondary_application_id=application, include_request_prefix=True
    )

    suffix = report["suffix"]
    assert isinstance(suffix, dict)
    prefix = suffix.pop("request_prefix")
    assert report == ordinary and report["reason"] == "exact_retained_secondary_suffix"
    assert prefix["status"] == {
        "exact": "recorded",
        "receipt": "recorded",
        "missing": "not_observed",
    }.get(control, "invalid")
    if control in ("exact", "receipt"):
        assert prefix["request_sha256"] == "a" * 64
        assert prefix["original_collection_application"] == "not_retained"
        assert prefix["retained_collection_checkpoint"] == "not_verified"
        assert prefix["prepared_input_receipt"] == (
            "validated_retained_receipt" if control == "receipt" else "not_retained"
        )
        assert (
            prefix["original_request_payload"]
            == prefix["source_selection_binding"]
            == "not_verified"
        )
    assert target.read_bytes() == before


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
@pytest.mark.parametrize("mode", ["primary", "compact", "secondary", "prefix", "census"])
def test_cli_cannot_write_database_sidecar(
    secondary_snapshot: tuple[Path, int, int],
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    suffix: str,
) -> None:
    target, publication, application = secondary_snapshot
    sidecar = Path(str(target) + suffix)
    args = ["--publication-id", str(publication)] if mode != "census" else ["--census"]
    if mode == "compact":
        args.append("--include-compact")
    if mode in ("secondary", "prefix"):
        args.extend(("--secondary-application-id", str(application)))
    if mode == "prefix":
        args.append("--include-request-prefix")
    monkeypatch.setattr(
        "sys.argv",
        ["primary_delivery_cycle", "--database", str(target), "--output", str(sidecar), *args],
    )

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 2
    assert not sidecar.exists()


def test_census_reuses_retained_secondary_evidence_without_writing(
    secondary_snapshot: tuple[Path, int, int],
) -> None:
    target, publication, application = secondary_snapshot
    before = target.read_bytes()

    report = census(target)
    individual = audit(
        target, publication, secondary_application_id=application, include_request_prefix=True
    )

    suffix = individual["suffix"]
    assert isinstance(suffix, dict), "The fixture has a retained secondary suffix"
    prefix = suffix["request_prefix"]
    assert prefix == {
        "status": "not_observed",
        "reason": "original_consumed_secondary_item_unavailable",
    }
    assert report["status"] == "recorded"
    assert report["secondary_census"] == [
        {
            "publication_id": publication,
            "application_id": application,
            "status": individual["status"],
            "reason": individual["reason"],
            "prefix": prefix,
        }
    ]
    assert target.read_bytes() == before


@pytest.mark.parametrize("secondary_snapshot", ["amendments"], indirect=True)
def test_secondary_question_requires_agreement_with_the_paid_result(
    secondary_snapshot: tuple[Path, int, int],
) -> None:
    target, publication, application = secondary_snapshot
    add_secondary_batch(target)
    with closing(sqlite3.connect(target)) as db, db:
        db.execute(
            "UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.amendment_input_ref',json(?))"
            " WHERE batch_id='secondary-batch'",
            (
                json.dumps(
                    {
                        "snapshot_id": "amendment-input-v1:" + "b" * 64,
                        "question_sha256": "b" * 64,
                        "request_sha256": "a" * 64,
                    }
                ),
            ),
        )

    report = audit(
        target, publication, secondary_application_id=application, include_request_prefix=True
    )

    suffix = report["suffix"]
    assert isinstance(suffix, dict)
    assert report["reason"] == "exact_retained_secondary_suffix"
    assert suffix["request_prefix"] == {
        "status": "invalid",
        "reason": "invalid_snapshot_evidence",
    }


@pytest.mark.parametrize("control", ["exact", "later_consumption", "provider", "usage"])
def test_secondary_exchange_checks_manifest_and_parsed_answer(
    secondary_snapshot: tuple[Path, int, int], control: str
) -> None:
    target, publication, application = secondary_snapshot
    add_secondary_batch(target)
    with closing(sqlite3.connect(target)) as db, db:
        paid = ComputationResult.model_validate_json(
            db.execute(
                "SELECT result_json FROM computation_results WHERE batch_id='secondary-batch'"
            ).fetchone()[0]
        )
        record = paid.record
        assert isinstance(record, (AmendmentsRecord, SupplementRecord)), (
            "the secondary fixture has a validated answer"
        )
        answer = record.amendments if isinstance(record, AmendmentsRecord) else record.digest
        sha = "a" * 64
        db.execute(
            "UPDATE llm_batch_items"
            " SET meta_json=json_set(meta_json,'$.frozen_request_manifest',json(?)), result_json=?"
            " WHERE batch_id='secondary-batch'",
            (
                json.dumps(
                    {
                        "provider": "anthropic" if control == "provider" else paid.provider,
                        "purpose": paid.purpose,
                        "request_sha256": sha,
                        "model": paid.model,
                        "prompt_version": "fixture",
                        "payload_bytes": 1,
                        "messages_sha256": sha,
                        "output_format_sha256": sha,
                        "options_sha256": sha,
                    }
                ),
                BatchResult(
                    custom_id=paid.custom_id,
                    answer=answer,
                    model=paid.model,
                    input_tokens=1 if control == "usage" else 0,
                ).model_dump_json(),
            ),
        )

        if control == "later_consumption":
            earlier = (NOW - timedelta(seconds=1)).isoformat()
            db.execute("UPDATE llm_batches SET submitted_at=?", (earlier,))
            db.execute(
                "UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.queued_at',?)",
                (earlier,),
            )
            db.execute(
                "UPDATE computation_results SET result_json=json_set(result_json,'$.collected_at',?)"
                " WHERE result_id=?",
                (earlier, paid.result_id),
            )

    report = audit(
        target, publication, secondary_application_id=application, include_request_prefix=True
    )

    suffix = report["suffix"]
    assert isinstance(suffix, dict)
    assert report["reason"] == "exact_retained_secondary_suffix"
    prefix = suffix["request_prefix"]
    if control in ("exact", "later_consumption"):
        assert prefix["retained_collection_checkpoint"] == (
            "matching_retained_checkpoint" if control == "exact" else "not_verified"
        )
        assert prefix["original_collection_application"] == "not_retained"
        assert prefix["retained_provider_request_manifest"] == "verified_retained_manifest"
        assert prefix["retained_provider_answer"] == "exact_retained_answer"
        assert prefix["original_request_payload"] == "not_verified"
    else:
        assert prefix == {"status": "invalid", "reason": "invalid_snapshot_evidence"}


def test_secondary_metadata_cycle_does_not_promote_a_partial_prefix(
    secondary_snapshot: tuple[Path, int, int],
) -> None:
    target, publication, application = secondary_snapshot
    add_secondary_batch(target, include_receipt=True)
    standard = audit(
        target, publication, secondary_application_id=application, include_request_prefix=True
    )
    qualified = audit(
        target,
        publication,
        secondary_application_id=application,
        include_secondary_metadata_cycle=True,
    )
    suffix = qualified["suffix"]
    assert isinstance(suffix, dict)
    prefix = suffix["request_prefix"]
    outcome = prefix.pop("metadata_cycle")
    assert outcome["status"] == "not_observed"
    assert outcome["reason"] in {
        "complete_amendment_source_receipt_unavailable",
        "outside_retained_amendment_profile",
    }
    assert suffix == standard["suffix"]

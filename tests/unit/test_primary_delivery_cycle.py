import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.primary_delivery_cycle import audit

from lexinform.models import (
    AmendmentsRecord,
    ComputationResult,
    DeliveryPlan,
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

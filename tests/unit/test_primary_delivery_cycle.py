import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.primary_delivery_cycle import audit

from lexinform.models import DeliveryPlan, Publication, PublicationKind, PublicationStatus
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

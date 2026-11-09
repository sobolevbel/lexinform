import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
from tools.primary_delivery_cycle import audit, census

from lexinform.adapters.content_history import (
    read_bound_card_primary_cycle,
    read_expired_card_primary_cycle,
)
from lexinform.models import Bill, PublicationKind, PublicationStatus
from lexinform.services.publishing import PublishingService
from tests.fakes import FakeSejmGateway, FixedClock
from tests.unit.test_card_content_writer import MultipartPublisher
from tests.unit.test_card_primary_evidence_reader import card_parents, store_marked_card
from tests.unit.test_content_sources import Case
from tests.unit.test_content_sources import case as case
from tests.unit.test_input_receipts import NOW


def checkpoint(case: Case, target: Path) -> None:
    with closing(sqlite3.connect(case.path)) as original, closing(sqlite3.connect(target)) as copy:
        original.backup(copy)


def test_compact_cycle_uses_only_the_opt_in_original_binding_after_plan_expiry(
    case: Case, tmp_path: Path
) -> None:
    pub, version = store_marked_card(case)
    assert pub.id is not None and version.rendered_text is not None, (
        "the original fixture supplies its persisted identity and frozen render"
    )
    case.repo.mark_publication(pub.id, PublicationStatus.SENT, message_id=10, sent_at=NOW)
    live = tmp_path / "with-plan.db"
    checkpoint(case, live)
    full = audit(live, pub.id)
    assert full["status"] == "recorded" and audit(live, pub.id, include_compact=True) == full
    with closing(sqlite3.connect(live)) as connection:
        assert read_expired_card_primary_cycle(connection, pub.id) is None
        bound_cycle = read_bound_card_primary_cycle(connection, pub.id)
        assert isinstance(full["cycle"], dict), "the direct report contains its verified cycle"
        assert bound_cycle == {**full["cycle"], "content_version_id": version.content_id}
        connection.execute(
            "UPDATE publications SET delivery_json=json_set(delivery_json,'$.rendered_text',?)"
            " WHERE id=?",
            ("Changed frozen render", pub.id),
        )
        with pytest.raises(ValueError, match="card_content_binding_scope"):
            read_bound_card_primary_cycle(connection, pub.id)
    case.repo.prune_history(before=NOW + timedelta(days=91), memo_before=NOW + timedelta(days=1))
    expired = tmp_path / "after-prune.db"
    checkpoint(case, expired)
    before = expired.read_bytes()

    default = audit(expired, pub.id)
    report = audit(expired, pub.id, include_compact=True)

    assert default["status"] == "not_observed" and default["reason"] == "frozen_plan_unavailable"
    assert report["status"] == "recorded" and report["reason"] == "exact_retained_compact_cycle"
    cycle = report["cycle"]
    full_cycle = full["cycle"]
    assert isinstance(cycle, dict) and isinstance(full_cycle, dict), (
        "recorded full and compact reports carry bounded cycle identities"
    )
    assert cycle == {
        **full_cycle,
        "content_version_id": version.content_id,
    }
    assert (
        cycle["frozen_render_sha256"] == hashlib.sha256(version.rendered_text.encode()).hexdigest()
    )
    assert all(word not in json.dumps(report) for word in ("Frozen card", "https://", "message_id"))
    assert expired.read_bytes() == before


@pytest.mark.parametrize("status", [PublicationStatus.QUEUED, PublicationStatus.UNKNOWN])
def test_compact_evidence_cannot_replace_physical_confirmation(
    case: Case, tmp_path: Path, status: PublicationStatus
) -> None:
    pub, _ = store_marked_card(case)
    assert pub.id is not None, "the fixture saved an original publication"
    case.execute("UPDATE publications SET delivery_json=NULL,status=? WHERE id=?", (status, pub.id))
    saved = tmp_path / "unconfirmed.db"
    checkpoint(case, saved)

    report = audit(saved, pub.id, include_compact=True)

    assert report["status"] == "not_observed"
    assert report["reason"] == "recorded_send_unavailable" and report["cycle"] is None


def test_compact_parent_corruption_remains_invalid(case: Case, tmp_path: Path) -> None:
    pub, version = store_marked_card(case)
    reference = version.card_primary_evidence
    assert pub.id is not None and reference is not None, (
        "the marked fixture supplies its exact source evidence"
    )
    case.repo.mark_publication(pub.id, PublicationStatus.SENT, message_id=10, sent_at=NOW)
    case.repo.prune_history(before=NOW + timedelta(days=91), memo_before=NOW + timedelta(days=1))
    case.execute(
        "DELETE FROM source_observations WHERE observation_id=?", (reference.source_observation_id,)
    )
    saved = tmp_path / "damaged.db"
    checkpoint(case, saved)

    report = audit(saved, pub.id, include_compact=True)

    assert report["status"] == "invalid"
    assert report["reason"] == "invalid_snapshot_evidence" and report["cycle"] is None


def test_multipart_primary_cycle_requires_exact_proof_and_every_ordered_send(
    case: Case, tmp_path: Path
) -> None:
    provisional, reference = card_parents(case)
    assert provisional.delivery is not None, "the fixture supplies an exact frozen paid answer"
    bill = Bill.model_validate_json(provisional.delivery.bill_json)
    case.repo.delete_publication(
        bill.term, bill.number, PublicationKind.NEW_BILL, provisional.channel_id
    )
    at = NOW + timedelta(days=1)
    publisher = MultipartPublisher(publication_by_id=case.repo.publication_by_id)
    service = PublishingService(
        FakeSejmGateway(),
        case.repo,
        publisher,
        FixedClock(at),
        channel_id=provisional.channel_id,
        attention_assess=False,
    )
    assert service.publish_bill(bill)
    root = service.card_of(bill)
    assert root is not None and root.id is not None and root.delivery is not None, (
        "the first send retained a physical root and a two-part freeze"
    )
    (child_id,) = root.delivery.continuation_ids
    live = tmp_path / "multipart-live.db"
    checkpoint(case, live)
    default = audit(live, root.id)
    assert default["reason"] == "publication_outside_direct_original_scope"
    report = audit(live, root.id, include_compact=True)
    assert report["status"] == "recorded" and report["reason"] == "exact_retained_multipart_cycle"
    assert census(live)["primary_census"] == {"publication_outside_direct_original_scope": 1}
    assert census(live, include_multipart=True)["primary_census"] == {
        "exact_retained_multipart_cycle": 1
    }
    cycle = report["cycle"]
    assert isinstance(cycle, dict), "the confirmed multipart profile records its exact proof"
    assert cycle["projection_application_id"] == reference.projection_application_id
    assert cycle["source_observation_id"] == reference.source_observation_id
    assert cycle["frozen_render_parts"] == [
        {
            "part_index": index,
            "recorded_sent_at": at.isoformat(),
            "frozen_render_sha256": hashlib.sha256(message.text.encode()).hexdigest(),
        }
        for index, message in enumerate(publisher.sent, 1)
    ]
    assert all(
        word not in json.dumps(report) for word in ("https://", "message_id", "continuation")
    )
    with closing(sqlite3.connect(live)) as copy:
        copy.execute("UPDATE publications SET status='unknown' WHERE id=?", (child_id,))
        copy.commit()
    assert audit(live, root.id, include_compact=True)["status"] == "not_observed"

    case.repo.prune_history(before=at + timedelta(days=1), memo_before=NOW)
    expired = tmp_path / "multipart-expired.db"
    checkpoint(case, expired)
    before = expired.read_bytes()
    assert audit(expired, root.id)["reason"] == "frozen_plan_unavailable"
    assert census(expired)["primary_census"] == {"frozen_plan_unavailable": 1}
    assert audit(expired, root.id, include_compact=True)["cycle"] == cycle
    assert expired.read_bytes() == before
    with closing(sqlite3.connect(expired)) as copy:
        copy.execute(
            "UPDATE publications SET sent_at=? WHERE id=?",
            ((at - timedelta(seconds=1)).isoformat(), child_id),
        )
        copy.commit()
    invalid = audit(expired, root.id, include_compact=True)
    assert invalid["status"] == "invalid" and invalid["cycle"] is None

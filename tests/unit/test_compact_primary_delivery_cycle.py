import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
from tools.primary_delivery_cycle import audit

from lexinform.adapters.content_history import (
    read_bound_card_primary_cycle,
    read_expired_card_primary_cycle,
)
from lexinform.models import PublicationStatus
from tests.unit.test_card_primary_evidence_reader import store_marked_card
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

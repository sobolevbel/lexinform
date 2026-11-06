import hashlib
from datetime import timedelta

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    Bill,
    BillStatus,
    ContentSnapshot,
    ContentVersion,
    DeliveryPlan,
    ProcessSummary,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
    StatusChange,
)
from lexinform.models.content import (
    MAX_DIRECT_STAGE_CONTENT_BYTES,
    DirectStageContentEvidence,
    card_bill_snapshot,
)
from lexinform.services.content_history import record_publication_content, restore_content_delivery
from lexinform.services.content_inspection import inspect_content
from tests.fakes import FixedClock
from tests.unit.test_content_history import NOW
from tests.unit.test_content_history import repo as repo


@pytest.mark.parametrize("first_freeze", [False, True])
def test_compact_direct_stage_history_survives_expiry_and_checks_its_event(
    repo: SqliteBillRepository,
    first_freeze: bool,
) -> None:
    summary = ProcessSummary(
        term=10, number="1", title="Projekt", document_type="projekt", change_date=NOW
    )
    bill = Bill(summary=summary, status=BillStatus.ANALYZED, first_seen_at=NOW, last_checked_at=NOW)
    root = Publication(
        term=10,
        number="1",
        channel_id="channel",
        kind=PublicationKind.NEW_BILL,
        status=PublicationStatus.SENT,
        created_at=NOW,
        sent_at=NOW,
        message_id=10,
    )
    anchor = repo.create_publication(root)
    change = StatusChange(
        term=10,
        number="1",
        old_fingerprint=None,
        new_fingerprint="senate",
        detected_at=NOW,
        new_stages=[Stage(stage_type="SenatePosition", stage_name="Stanowisko Senatu")],
    )
    change_id = repo.add_status_change(change)
    assert change_id is not None, "the fixture records a new legislative stage"
    change = change.model_copy(update={"id": change_id})
    plan = DeliveryPlan(
        bill_json=bill.model_dump_json(),
        change_json=change.model_dump_json(),
        rendered_text="Frozen stage update",
        today=NOW.date(),
        reply_to=10,
    )
    pub_id = repo.create_publication(
        root.model_copy(
            update={
                "kind": PublicationKind.STATUS_UPDATE,
                "status_change_id": change_id,
                "message_id": 11,
                "delivery": plan,
                "status": PublicationStatus.QUEUED if first_freeze else PublicationStatus.SENT,
                "sent_at": None if first_freeze else NOW,
            }
        )
    )
    bill_snapshot = card_bill_snapshot(plan.bill_json)
    change_snapshot = ContentSnapshot(kind="change", payload_json=plan.change_json)
    plan_snapshot = ContentSnapshot(
        kind="plan",
        payload_json=plan.model_copy(
            update={"bill_json": "", "change_json": "", "rendered_text": ""}
        ).model_dump_json(),
    )
    for snapshot in (bill_snapshot, change_snapshot, plan_snapshot):
        repo.save_content_snapshot(snapshot)
    evidence = DirectStageContentEvidence(
        anchor_publication_id=anchor,
        original_bill_sha256=hashlib.sha256(plan.bill_json.encode()).hexdigest(),
    )
    version = ContentVersion(
        root_publication_id=pub_id,
        reason="original",
        language="ru",
        bill_snapshot_id=bill_snapshot.snapshot_id,
        change_snapshot_id=change_snapshot.snapshot_id,
        plan_snapshot_id=plan_snapshot.snapshot_id,
        rendered_text=plan.rendered_text,
        formatter_version="telegram-format-v2",
        status_change_id=change_id,
        recorded_at=NOW,
        direct_stage_evidence=evidence,
    )
    wrong_hash = evidence.model_copy(update={"original_bill_sha256": "0" * 64})
    with pytest.raises(ValueError, match="direct_stage_frozen_delivery_mismatch"):
        repo.save_content_version(version.model_copy(update={"direct_stage_evidence": wrong_hash}))
    with pytest.raises(ValueError, match="requires a bounded original"):
        repo.save_content_version(
            version.model_copy(update={"rendered_text": "ą" * MAX_DIRECT_STAGE_CONTENT_BYTES})
        )
    if first_freeze:
        frozen = repo.publication_by_id(pub_id)
        assert frozen is not None, "the fixture retains its freshly frozen update"
        assert record_publication_content(repo, FixedClock(NOW), frozen) is None
        origin = frozen.model_copy(
            update={"delivery": plan.model_copy(update={"rendered_text": ""})}
        )
        assert (
            record_publication_content(
                repo,
                FixedClock(NOW),
                frozen,
                application_origin=origin,
                formatter_version="telegram-format-v2",
                language="ru",
            )
            == version
        )
        repo.mark_publication(pub_id, PublicationStatus.SENT, sent_at=NOW, message_id=11)
    else:
        repo.save_content_version(version)
        repo.bind_publication_content(pub_id, version.content_id)
    repo.prune_history(before=NOW + timedelta(days=1), memo_before=NOW)
    dump = repo.dump()
    repo.restore(dump)

    sent = repo.publication_by_id(pub_id)
    assert sent is not None and sent.delivery is None
    history = inspect_content(repo, sent)
    assert history is not None and history.current_id == version.content_id
    assert repo.get_publication_content(pub_id) == version
    with pytest.raises(ValueError, match="does not retain the full original context"):
        restore_content_delivery(repo, version)
    changed = dump.replace("COMMIT;", "UPDATE status_changes SET new_fingerprint='wrong';\nCOMMIT;")
    with pytest.raises(ValueError, match="invalid_content_history"):
        repo.restore(changed)
    assert repo.dump() == dump

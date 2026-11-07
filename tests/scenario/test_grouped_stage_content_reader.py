import hashlib
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import Literal

import pytest
from tools.content_history_doctor import audit

from lexinform.adapters.content_history import read_grouped_stage_telegram_target
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.content_versions import grouped_stage_context
from lexinform.errors import DeliveryUncertainError
from lexinform.models import ContentSnapshot, ContentVersion, PublicationStatus
from lexinform.models.content import (
    GroupedStageContentEvidence,
    GroupedStageMember,
    card_bill_snapshot,
)
from lexinform.services import posting
from lexinform.services.content_history import restore_content_delivery
from lexinform.services.content_inspection import inspect_content
from tests.checkpoint_state import saved_state
from tests.harness import World
from tests.scenario.test_group_remote_checkpoints import flush, queued_world, rows


@pytest.fixture(autouse=True)
def isolate_manual_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(posting, "record_grouped_stage_content", lambda *args, **kwargs: None)


def version_fixture(
    world: World, identities: tuple[int, ...]
) -> tuple[ContentVersion, tuple[ContentSnapshot, ...]]:
    root, second = rows(world, identities)
    snapshots: list[ContentSnapshot] = []
    members: list[GroupedStageMember] = []
    for publication in (root, second):
        plan = publication.delivery
        anchor = world.publication(publication.number)
        assert (
            plan is not None
            and anchor is not None
            and anchor.id is not None
            and anchor.message_id is not None
            and publication.id is not None
            and publication.status_change_id is not None
        ), "the exact original group fixture retains stage updates and both card receipts"
        bill = card_bill_snapshot(plan.bill_json)
        change = ContentSnapshot(kind="change", payload_json=plan.change_json)
        snapshots.extend((bill, change))
        members.append(
            GroupedStageMember(
                publication_id=publication.id,
                anchor_publication_id=anchor.id,
                anchor_message_id=anchor.message_id,
                status_change_id=publication.status_change_id,
                bill_snapshot_id=bill.snapshot_id,
                change_snapshot_id=change.snapshot_id,
                original_bill_sha256=hashlib.sha256(plan.bill_json.encode()).hexdigest(),
            )
        )
    plan = root.delivery
    assert plan is not None, "the root retains the shared render"
    compact = ContentSnapshot(
        kind="plan", payload_json=grouped_stage_context(plan).model_dump_json()
    )
    snapshots.append(compact)
    first, last = members
    return (
        ContentVersion(
            root_publication_id=first.publication_id,
            reason="original",
            language="ru",
            formatter_version="telegram-format-v2",
            bill_snapshot_id=first.bill_snapshot_id,
            change_snapshot_id=first.change_snapshot_id,
            plan_snapshot_id=compact.snapshot_id,
            rendered_text=plan.group_text,
            status_change_id=first.status_change_id,
            grouped_stage_evidence=GroupedStageContentEvidence(members=(first, last)),
            recorded_at=world.clock.now(),
        ),
        tuple(snapshots),
    )


def attach(world: World, version: ContentVersion, snapshots: tuple[ContentSnapshot, ...]) -> None:
    with world.repo.atomic():
        for snapshot in snapshots:
            world.repo.save_content_snapshot(snapshot)
        world.repo.save_content_version(version)
        world.repo.bind_publication_content(version.root_publication_id, version.content_id)


@pytest.mark.parametrize("outcome", ["sent", "unknown"])
def test_grouped_content_keeps_exact_render_and_shared_confirmation_after_restore(
    outcome: Literal["sent", "unknown"], monkeypatch: pytest.MonkeyPatch
) -> None:
    world, identities = queued_world("updates")
    if outcome == "unknown":

        def uncertain(text: str) -> int:
            raise DeliveryUncertainError("response lost")

        monkeypatch.setattr(world.publisher, "publish_updates", uncertain)
        with pytest.raises(DeliveryUncertainError):
            flush(world, "updates", identities)
    else:
        flush(world, "updates", identities)
    root, second = rows(world, identities)
    assert world.repo.get_publication_content(identities[0]) is None
    assert root.status.value == second.status.value == outcome
    version, snapshots = version_fixture(world, identities)

    attach(world, version, snapshots)

    if outcome == "sent":
        before = world.clock.now() + timedelta(days=1)
        world.repo.prune_history(before=before, memo_before=before)
        assert all(member.delivery is None for member in rows(world, identities))
    dumped = world.repo.dump()
    for _ in range(2):
        with saved_state(dumped) as restored:
            current, other = rows(restored, identities)
            assert current.status.value == other.status.value == outcome
            assert restored.repo.get_publication_content(identities[0]) == version
            assert restored.repo.get_publication_content(identities[1]) is None
            history = inspect_content(restored.repo, current)
            assert history is not None and history.integrity == "valid"
            assert history.current_id == (version.content_id if outcome == "sent" else None)
            with pytest.raises(ValueError, match="does not retain correction context"):
                restore_content_delivery(restored.repo, version)
            assert restored.repo.dump() == dumped


def test_grouped_reader_refuses_wrong_member_and_receipt() -> None:
    world, identities = queued_world("updates")
    flush(world, "updates", identities)
    version, snapshots = version_fixture(world, identities)
    evidence = version.grouped_stage_evidence
    assert evidence is not None, "the fixture supplies both ordered member witnesses"
    root, second = evidence.members
    wrong = version.model_copy(
        update={
            "grouped_stage_evidence": evidence.model_copy(
                update={"members": (root, second.model_copy(update={"anchor_message_id": 9999}))}
            )
        }
    )
    with pytest.raises(ValueError, match="grouped_stage_publication_scope"):
        attach(world, wrong, snapshots)
    assert world.repo.get_content_snapshot(snapshots[0].snapshot_id) is None

    world.repo.mark_publication(
        identities[1], PublicationStatus.SENT, message_id=9999, sent_at=world.clock.now()
    )
    with pytest.raises(ValueError, match="grouped_stage_receipt"):
        attach(world, version, snapshots)


def test_grouped_reader_rejects_reordered_frozen_members_during_restore() -> None:
    world, identities = queued_world("updates")
    flush(world, "updates", identities)
    version, snapshots = version_fixture(world, identities)
    attach(world, version, snapshots)
    before = world.repo.dump()
    command = (
        "UPDATE publications SET delivery_json=json_set(delivery_json, '$.update_group',"
        " json_array(json_extract(delivery_json, '$.update_group[1]'),"
        " json_extract(delivery_json, '$.update_group[0]')))"
        f" WHERE id={identities[0]};\n"
    )
    broken = before.replace("COMMIT;\n", command + "COMMIT;\n", 1)
    assert broken != before

    with pytest.raises(ValueError, match="invalid_content_history"):
        world.repo.restore(broken)
    assert world.repo.dump() == before


def test_grouped_reader_survives_one_member_expiring_before_the_other() -> None:
    world, identities = queued_world("updates")
    flush(world, "updates", identities)
    _, second = rows(world, identities)
    assert second.message_id is not None and second.sent_at is not None, (
        "the existing group publishes both owners with the same message ID"
    )
    before = second.sent_at + timedelta(microseconds=1)
    world.repo.mark_publication(
        identities[1], PublicationStatus.SENT, message_id=second.message_id, sent_at=before
    )
    version, snapshots = version_fixture(world, identities)
    attach(world, version, snapshots)

    world.repo.prune_history(before=before, memo_before=before)

    with saved_state(world.repo.dump()) as restored:
        root, second = rows(restored, identities)
        assert root.delivery is None and second.delivery is not None
        history = inspect_content(restored.repo, root)
        assert history is not None and history.current_id == version.content_id


@pytest.mark.parametrize("control", ["exact", "partial", "conflict"])
def test_grouped_numeric_receipt_requires_both_original_rows(tmp_path: Path, control: str) -> None:
    world, identities = queued_world("updates")
    flush(world, "updates", identities)
    version, snapshots = version_fixture(world, identities)
    attach(world, version, snapshots)
    before = world.clock.now() + timedelta(days=1)
    world.repo.prune_history(before=before, memo_before=before)
    path = tmp_path / "grouped-receipt.db"
    chat_id = -1001234567890
    with closing(SqliteBillRepository(path)) as repo:
        repo.restore(world.repo.dump())
        legacy = repo.dump()
        with closing(sqlite3.connect(path)) as db, db:
            assert read_grouped_stage_telegram_target(db, version) is None
            db.execute(
                "UPDATE publications SET telegram_chat_id=? WHERE id=?", (chat_id, identities[0])
            )
            if control != "partial":
                second_chat_id = chat_id - int(control == "conflict")
                db.execute(
                    "UPDATE publications SET telegram_chat_id=? WHERE id=?",
                    (second_chat_id, identities[1]),
                )
        prospective = repo.dump()
        audit_path = tmp_path / "checkpoint.db"
        with (
            closing(sqlite3.connect(path)) as source,
            closing(sqlite3.connect(audit_path)) as checkpoint,
        ):
            source.backup(checkpoint)
        report = audit(audit_path)
        assert str(chat_id) not in str(report)
        if control != "exact":
            assert report["status"] == "failed"
            assert "grouped_numeric_telegram_receipts" not in report["summary"]
            with (
                closing(sqlite3.connect(audit_path)) as db,
                pytest.raises(ValueError, match="grouped_stage_receipt"),
            ):
                read_grouped_stage_telegram_target(db, version)
            repo.restore(legacy)
            with pytest.raises(ValueError, match="invalid_content_history"):
                repo.restore(prospective)
            assert repo.dump() == legacy
            return
        assert report["summary"]["grouped_numeric_telegram_receipts"] == 1
        for _ in range(2):
            repo.restore(prospective)
            assert repo.dump() == prospective
        root = repo.publication_by_id(identities[0])
        assert root is not None and root.telegram_chat_id == chat_id
        assert root.delivery is None and root.channel_id == rows(world, identities)[0].channel_id
        reset = root.model_copy(
            update={"status": PublicationStatus.QUEUED, "telegram_chat_id": None}
        )
        assert repo.create_publication(reset) == root.id
        assert repo.dump() == prospective
        with closing(sqlite3.connect(path)) as db, db:
            assert read_grouped_stage_telegram_target(db, version) == (chat_id, root.message_id)
            for statement in (
                "UPDATE publications SET telegram_chat_id=1 WHERE id=?",
                "UPDATE publications SET telegram_chat_id=-4503599627370496 WHERE id=?",
                "UPDATE publications SET status='unknown' WHERE id=?",
                "UPDATE publications SET kind='new_bill' WHERE id=?",
                "UPDATE publications SET message_id=NULL WHERE id=?",
            ):
                with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
                    db.execute(statement, (root.id,))

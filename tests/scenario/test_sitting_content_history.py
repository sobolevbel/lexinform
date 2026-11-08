import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
from tools.content_history_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    Bill,
    ContentSnapshot,
    ContentVersion,
    PublicationKind,
    PublicationStatus,
)
from lexinform.models.content import unpack_original_bill_snapshot
from lexinform.models.sitting_content import SittingContentPlan
from lexinform.services.content_inspection import inspect_content
from tests.checkpoint_state import saved_state
from tests.harness import CHANNEL, COMMITTEE_STAGES, World
from tests.scenario.test_long_sitting_remote_checkpoints import flush, identities, queued, row
from tests.scenario.test_tracking_agenda import ASW, _sitting


def short_sittings(*, grouped: bool) -> tuple[World, tuple[int, ...], int]:
    world = World(profile="production", reader_attention="off")
    world.gateway.committees["ASW"] = ASW
    numbers = ("3039", "3055") if grouped else ("3039",)
    for number in numbers:
        world.add_bill(number, "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES)
    world.run()
    world.batch.resolve()
    world.run()
    world.gateway.committee_sittings["ASW"] = tuple(
        _sitting(num=136 + index, agenda=f"<div>Pierwsze czytanie (druk nr {number})</div>")
        for index, number in enumerate(numbers)
    )
    world.run(publish=False)
    before = len(world.publisher.sent)
    open_sittings = [
        pub
        for pub in world.repo.list_open_deliveries(CHANNEL)
        if pub.kind is PublicationKind.AGENDA
    ]
    assert len(open_sittings) == len(numbers)
    world.run()
    logical = []
    for pub in open_sittings:
        assert pub.id is not None, "the agenda watcher assigned the identity"
        logical.append(pub.id)
    return world, tuple(logical), before


@pytest.mark.parametrize("profile", ["short", "group", "multipart", "packed-multipart"])
def test_sitting_content_survives_normal_delivery_cleanup_and_restore(
    profile: str, tmp_path: Path
) -> None:
    logical: tuple[int, ...]
    multipart = profile in ("multipart", "packed-multipart")
    packed_bill_json = None
    if multipart:
        case = queued()
        world = case.world
        if profile == "packed-multipart":
            queued_plan = row(world, case.identity).delivery
            assert queued_plan is not None, "the agenda watcher saved the original plan"
            bill = Bill.model_validate_json(queued_plan.bill_json)
            enlarged = bill.model_copy(
                update={"summary": bill.summary.model_copy(update={"description": "x" * 150_000})}
            )
            packed_bill_json = enlarged.model_dump_json()
            assert world.repo.replace_unattempted_delivery(
                case.identity, queued_plan.model_copy(update={"bill_json": packed_bill_json})
            )
        before = len(world.publisher.sent)
        flush(case)
        bound = identities(world, case.identity)
        logical = (case.identity,)
    else:
        world, logical, before = short_sittings(grouped=profile == "group")
        bound = logical
    original = tuple(row(world, identity) for identity in bound)
    sent = tuple(message.text for message in world.publisher.sent[before:])
    assert len(sent) == (len(bound) if multipart else 1)
    assert all(pub.status is PublicationStatus.SENT and pub.message_id for pub in original)
    database = tmp_path / "sitting.db"
    with closing(SqliteBillRepository(database)) as repo:
        repo.migrate()
        repo.restore(world.repo.dump())
    report = audit(database, publication_id=bound[0])
    assert report["status"] == "recorded" and not report["findings"]
    (history,) = report["histories"]
    assert history["current_content_id"] is not None
    if multipart:
        with closing(sqlite3.connect(database)) as db, db:
            db.execute(
                "UPDATE publications SET status='queued', message_id=NULL, sent_at=NULL WHERE id=?",
                (bound[-1],),
            )
        pending = audit(database, publication_id=bound[0])
        assert pending["status"] == "recorded" and not pending["findings"]
        assert pending["histories"][0]["current_content_id"] is None

        with closing(sqlite3.connect(database)) as db, db:
            db.execute("DELETE FROM publication_content WHERE publication_id=?", (bound[-1],))
        corrupted = audit(database, publication_id=bound[0])
        assert corrupted["status"] == "failed"
        assert "invalid_sitting_content_history" in {row["code"] for row in corrupted["findings"]}
        assert corrupted["histories"][0]["current_content_id"] is None

    cutoff = world.clock.now() + timedelta(days=999)
    world.repo.prune_history(before=cutoff, memo_before=cutoff)
    sql = world.repo.dump()
    for _ in range(2):
        with saved_state(sql) as restored:
            assert restored.repo.dump() == sql
            for pub, identity in zip(original, bound, strict=True):
                current = row(restored, identity)
                assert current.delivery is None and current.message_id == pub.message_id
                version = restored.repo.get_publication_content(identity)
                assert version is not None, "all logical and physical identities share the version"
                view = inspect_content(restored.repo, current)
                assert view is not None and view.current_id == version.content_id
                snapshot = restored.repo.get_content_snapshot(version.plan_snapshot_id)
                assert snapshot is not None, "the version references its immutable sitting plan"
                plan = SittingContentPlan.model_validate_json(snapshot.payload_json)
                assert plan.bindings == bound
                assert tuple(member.id for member in plan.members) == logical
                assert tuple(part.html for part in plan.parts) == sent
                if packed_bill_json is not None:
                    (member,) = plan.members
                    assert member.packed_bill is True
                    bill_snapshot = restored.repo.get_content_snapshot(member.bill_snapshot_id)
                    assert bill_snapshot is not None, "the sitting references its packed context"
                    assert (
                        unpack_original_bill_snapshot(bill_snapshot).payload_json
                        == packed_bill_json
                    )


def test_sitting_version_reuses_identical_bill_snapshot() -> None:
    case = queued()
    repo = case.world.repo
    saved = row(case.world, case.identity).delivery
    assert saved is not None, "the watcher froze the bill before rendering"
    bill = ContentSnapshot(kind="bill", payload_json=saved.bill_json)
    repo.save_content_snapshot(bill)
    expected = repo.dump().count(f"VALUES('{bill.snapshot_id}','bill',")
    assert expected == 1
    flush(case)
    version = repo.get_publication_content(case.identity)
    assert version is not None, "the ordinary sitting freeze wrote its version"
    plan = repo.get_content_snapshot(version.plan_snapshot_id)
    assert plan is not None, "the version references the retained semantic plan"
    assert SittingContentPlan.model_validate_json(plan.payload_json).members[
        0
    ].bill_snapshot_id == (bill.snapshot_id)
    assert repo.dump().count(f"VALUES('{bill.snapshot_id}','bill',") == expected


def test_failed_sitting_history_insert_rolls_back_all_frozen_parts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = queued("off")
    world = case.world
    sql = world.repo.dump()
    sent = tuple(world.publisher.sent)
    original = world.repo.save_content_version

    def fail(version: ContentVersion) -> None:
        original(version)
        if version.sitting_profile is not None:
            raise ValueError("injected failure after the history insert")

    monkeypatch.setattr(world.repo, "save_content_version", fail)
    with pytest.raises(ValueError, match="injected failure"):
        flush(case)
    assert world.repo.dump() == sql
    assert tuple(world.publisher.sent) == sent

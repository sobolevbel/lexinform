from datetime import timedelta

import pytest

from lexinform.models import ContentSnapshot, ContentVersion, PublicationKind, PublicationStatus
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


@pytest.mark.parametrize("profile", ["short", "group", "multipart"])
def test_sitting_content_survives_normal_delivery_cleanup_and_restore(profile: str) -> None:
    logical: tuple[int, ...]
    if profile == "multipart":
        case = queued()
        world = case.world
        before = len(world.publisher.sent)
        flush(case)
        bound = identities(world, case.identity)
        logical = (case.identity,)
    else:
        world, logical, before = short_sittings(grouped=profile == "group")
        bound = logical
    original = tuple(row(world, identity) for identity in bound)
    sent = tuple(message.text for message in world.publisher.sent[before:])
    assert len(sent) == (len(bound) if profile == "multipart" else 1)
    assert all(pub.status is PublicationStatus.SENT and pub.message_id for pub in original)
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

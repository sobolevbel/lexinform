from datetime import timedelta
from pathlib import Path

import pytest

from lexinform.errors import DeliveryUncertainError
from lexinform.models import (
    AmendmentsRecord,
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
    StatusChange,
)
from lexinform.models.original_delivery_content import OriginalDeliveryPlan
from lexinform.ports import PublishResult
from lexinform.services import content_history
from lexinform.services.content_inspection import inspect_content
from tests.fakes import make_amendments
from tests.harness import CHANNEL, World
from tests.scenario.test_group_remote_checkpoints import flush


@pytest.mark.parametrize("outcome", ["sent", "unknown", "no_anchor", "budget"])
def test_direct_multipart_history_follows_original_physical_send(
    outcome: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    world = World(profile="production", reader_attention="off")
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    world.batch.resolve()
    world.run()
    card = world.publication("3039", PublicationKind.NEW_BILL)
    assert card is not None and card.message_id is not None
    bill = world.bill("3039")
    now = world.clock.now()
    change = StatusChange(
        term=bill.term,
        number=bill.number,
        old_fingerprint=None,
        new_fingerprint="senate",
        detected_at=now,
        new_stages=[Stage(stage_type="SenatePosition", stage_name="Stanowisko Senatu")],
    )
    change_id = world.repo.add_status_change(change)
    assert change_id is not None
    root = world.repo.create_publication(
        Publication(
            term=bill.term,
            number=bill.number,
            channel_id=CHANNEL,
            kind=PublicationKind.STATUS_UPDATE,
            status=PublicationStatus.QUEUED,
            created_at=now,
            status_change_id=change_id,
            delivery=DeliveryPlan(
                bill_json=bill.model_dump_json(),
                change_json=change.model_copy(update={"id": change_id}).model_dump_json(),
                reply_to=card.message_id,
                today=now.date(),
            ),
        )
    )
    if outcome == "no_anchor":
        monkeypatch.setattr(world.repo, "card_aliases", lambda channel_id, message_id: [])
    if outcome == "budget":
        monkeypatch.setattr(content_history, "MAX_ORIGINAL_DELIVERY_BYTES", 1)
    render = world.publisher.render_delivery

    def long(publication: Publication) -> str:
        text = render(publication)
        return text + "\n" + "Срок 30 дней. " * 650 if publication.id == root else text

    monkeypatch.setattr(world.publisher, "render_delivery", long)
    send = world.publisher.publish_prepared

    def send_checked(publication: Publication) -> PublishResult:
        if publication.id == root and outcome in ("sent", "unknown"):
            frozen = world.repo.get_publication_content(root)
            assert frozen is not None and frozen.direct_stage_evidence is not None
            assert frozen.direct_stage_evidence.multipart == "direct-stage-parts-v1"
            assert publication.delivery is not None
            assert all(
                world.repo.get_publication_content(identity) == frozen
                for identity in publication.delivery.continuation_ids
            )
        result = send(publication)
        if outcome == "unknown" and publication.kind is PublicationKind.MESSAGE_PART:
            raise DeliveryUncertainError("accepted but response lost")
        return result

    monkeypatch.setattr(world.publisher, "publish_prepared", send_checked)
    flush(world, "updates", (root,))
    if outcome == "unknown":
        world.container.delivery_service(dry_run=False).run()
    physical = world.repo.publication_by_id(root)
    assert physical is not None and physical.delivery is not None
    assert len(physical.delivery.continuation_ids) >= 1
    if outcome in ("sent", "budget"):
        world.repo.save_status_change_amendments(
            change_id,
            AmendmentsRecord(
                amendments=make_amendments(),
                model="fixture",
                prompt_version="fixture",
                source_url="https://example.test/amendments",
                source_kind="senate_amendments",
                created_at=now,
            ),
        )
    history = world.repo.get_publication_content(root)
    if outcome in ("no_anchor", "budget"):
        assert history is None
        if outcome == "budget":
            cutoff = now + timedelta(days=100)
            world.repo.prune_history(before=cutoff, memo_before=cutoff)
            (tmp_path / "baseline.sql").write_text(world.repo.dump())
        return
    assert history is not None and history.direct_stage_evidence is not None
    assert history.direct_stage_evidence.multipart == "direct-stage-parts-v1"
    snap = world.repo.get_content_snapshot(history.plan_snapshot_id)
    assert snap is not None
    plan = OriginalDeliveryPlan.model_validate_json(snap.payload_json)
    assert plan.bindings == (root, *physical.delivery.continuation_ids)
    expected = [
        item.text
        for item in world.publisher.sent
        if item.kind in (PublicationKind.STATUS_UPDATE, PublicationKind.MESSAGE_PART)
    ]
    assert [part.html for part in plan.parts[: len(expected)]] == expected
    if outcome == "sent":
        cutoff = now + timedelta(days=100)
        world.repo.prune_history(before=cutoff, memo_before=cutoff)
    sql = world.repo.dump()
    (tmp_path / f"{outcome}.sql").write_text(sql)
    for command in (
        f"DELETE FROM publication_content WHERE publication_id={plan.bindings[-1]}",
        f"UPDATE publications SET channel_id='foreign' WHERE id={plan.bindings[-1]}",
        f"UPDATE publications SET message_id=0 WHERE id={root}",
        f"UPDATE publications SET message_id=9999 WHERE id={card.id}",
    ):
        with pytest.raises(ValueError, match="invalid_content_history"):
            world.repo.restore(sql + "\n" + command + ";\n")
        assert world.repo.dump() == sql
    for _ in range(2):
        world.repo.restore(sql)
        assert world.repo.dump() == sql
        for identity in plan.bindings:
            row = world.repo.publication_by_id(identity)
            assert row is not None
            assert world.repo.get_publication_content(identity) == history
            view = inspect_content(world.repo, row)
            assert view is not None and view.current_id == (
                history.content_id if outcome == "sent" else None
            )

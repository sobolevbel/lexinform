from datetime import timedelta

import pytest

from lexinform.models import (
    AmendmentsRecord,
    Publication,
    PublicationKind,
    StatusChange,
    SupplementRecord,
)
from lexinform.models.events import update_event
from lexinform.models.original_delivery_content import OriginalDeliveryPlan
from lexinform.services.content_inspection import inspect_content
from tests.checkpoint_state import saved_state
from tests.fakes import make_amendments
from tests.harness import COMMITTEE_STAGES, World


@pytest.mark.parametrize("multipart", [False, True])
def test_normal_multistage_update_keeps_order_and_latest_event_after_cleanup(
    multipart: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(profile="production", reader_attention="off")
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    world.batch.resolve()
    world.run()
    world.set_stages("3039", COMMITTEE_STAGES)
    world.clock.advance(days=1)
    if multipart:
        render = world.publisher.render_delivery

        def long_update(publication: Publication) -> str:
            text = render(publication)
            return (
                text + "\n" + "Срок 30 дней. " * 650
                if publication.kind is PublicationKind.STATUS_UPDATE
                else text
            )

        monkeypatch.setattr(world.publisher, "render_delivery", long_update)
    before = len(world.publisher.sent)
    assert world.run().updates == 1
    root = world.publication("3039", PublicationKind.STATUS_UPDATE)
    assert root is not None and root.id is not None and root.delivery is not None
    ids = (root.id, *root.delivery.continuation_ids)
    assert (len(ids) > 1) == multipart
    assert root.status_change_id is not None
    version = world.repo.get_publication_content(root.id)
    assert version is not None and version.direct_stage_evidence is not None
    assert version.change_snapshot_id is not None
    change = world.repo.get_status_change(root.status_change_id)
    assert change is not None
    assert [stage.stage_type for stage in change.new_stages] == ["ReadingReferral", "Referral"]
    event_key = update_event(change, world.bill("3039"))
    assert event_key == "referral"
    sent = world.publisher.sent[before:]
    assert len(sent) == len(ids)
    now = world.clock.now()
    world.repo.save_status_change_amendments(
        root.status_change_id,
        AmendmentsRecord(
            amendments=make_amendments(),
            model="fixture",
            prompt_version="fixture",
            source_url="https://example.test/amendments",
            source_kind="senate_amendments",
            created_at=now,
        ),
    )
    world.repo.save_status_change_supplements(
        root.status_change_id,
        [
            SupplementRecord(
                number="3039-A",
                title="Stanowisko rządu",
                source_kind="government_position",
                source_url="https://example.test/supplement",
            )
        ],
    )
    cutoff = now + timedelta(days=999)
    world.repo.prune_history(before=cutoff, memo_before=cutoff)
    sql = world.repo.dump()
    for _ in range(2):
        with saved_state(sql) as durable:
            assert durable.repo.dump() == sql
            saved_change = durable.repo.get_content_snapshot(version.change_snapshot_id)
            assert saved_change is not None
            original = StatusChange.model_validate_json(saved_change.payload_json)
            assert original == change and update_event(original, world.bill("3039")) == event_key
            current = durable.repo.get_status_change(root.status_change_id)
            assert current is not None and current.amendments is not None
            assert current.supplements and current.new_stages == original.new_stages
            for identity in ids:
                row = durable.repo.publication_by_id(identity)
                assert row is not None and row.delivery is None and row.message_id
                assert durable.repo.get_publication_content(identity) == version
                view = inspect_content(durable.repo, row)
                assert view is not None and view.current_id == version.content_id
            if multipart:
                saved_plan = durable.repo.get_content_snapshot(version.plan_snapshot_id)
                assert saved_plan is not None
                plan = OriginalDeliveryPlan.model_validate_json(saved_plan.payload_json)
                assert plan.bindings == ids
                assert [part.html for part in plan.parts] == [message.text for message in sent]
            else:
                assert version.rendered_text == sent[0].text
            corrupted = sql.replace(
                "COMMIT;",
                "UPDATE status_changes SET new_stages_json = "
                "json_array(json_extract(new_stages_json, '$[1]'), "
                "json_extract(new_stages_json, '$[0]')) "
                f"WHERE id={root.status_change_id};\nCOMMIT;",
            )
            with pytest.raises(ValueError, match="invalid_content_history"):
                durable.repo.restore(corrupted)
            assert durable.repo.dump() == sql
            wrong_owner = sql.replace(
                "COMMIT;",
                f"UPDATE status_changes SET number='9999' WHERE id={root.status_change_id};\nCOMMIT;",
            )
            with pytest.raises(ValueError, match="invalid_content_history"):
                durable.repo.restore(wrong_owner)
            assert durable.repo.dump() == sql

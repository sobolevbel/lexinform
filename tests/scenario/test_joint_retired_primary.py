from datetime import timedelta

import pytest

from lexinform.models import PublicationKind
from lexinform.models.original_delivery_content import OriginalDeliveryPlan
from lexinform.services.content_inspection import inspect_content
from tests.checkpoint_state import saved_state
from tests.scenario.test_card_remote_checkpoints import ready_world
from tests.scenario.test_commands import _commands_only


def test_republish_primary_preserves_the_original_joint_reply() -> None:
    world = ready_world("off", kind=PublicationKind.JOINT_BILL)
    world.run(track=False)
    primary = world.publication("3038")
    joint = world.publication("3039", PublicationKind.JOINT_BILL)
    assert primary is not None and primary.id is not None and primary.message_id is not None
    assert joint is not None and joint.id is not None and joint.message_id is not None
    version = world.repo.get_publication_content(joint.id)
    assert version is not None and version.original_delivery_evidence is not None
    evidence = version.original_delivery_evidence.joint_primary
    assert evidence is not None and evidence.publication_id == primary.id
    world.command("/republish 3038")
    assert not _commands_only(world).commands_failed
    replacement = world.publication("3038")
    assert replacement is not None and replacement.id != primary.id
    assert replacement.message_id != primary.message_id
    assert world.repo.publication_by_id(primary.id) is None
    cutoff = world.clock.now() + timedelta(days=999)
    world.repo.prune_history(before=cutoff, memo_before=cutoff)
    sql = world.repo.dump()
    for _ in range(2):
        with saved_state(sql) as restored:
            assert restored.repo.dump() == sql
            physical = restored.repo.publication_by_id(joint.id)
            assert physical is not None and physical.message_id == joint.message_id
            assert restored.repo.get_publication_content(joint.id) == version
            view = inspect_content(restored.repo, physical)
            assert view is not None and view.current_id == version.content_id
            snapshot = restored.repo.get_content_snapshot(version.plan_snapshot_id)
            assert snapshot is not None
            plan = OriginalDeliveryPlan.model_validate_json(snapshot.payload_json)
            assert plan.root.reply_to == primary.message_id
            for mutation in (
                f"DELETE FROM retired_stage_anchors WHERE id={primary.id}",
                "UPDATE retired_stage_anchors "
                f"SET message_id={replacement.message_id} WHERE id={primary.id}",
                "UPDATE retired_stage_anchors "
                f"SET sent_at='2099-01-01T00:00:00+00:00' WHERE id={primary.id}",
            ):
                corrupted = sql.replace("COMMIT;", f"{mutation};\nCOMMIT;")
                with pytest.raises(ValueError, match="invalid_content_history"):
                    restored.repo.restore(corrupted)
                assert restored.repo.dump() == sql

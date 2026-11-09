from datetime import timedelta

import pytest

from lexinform.models import Bill, PublicationKind
from lexinform.models.original_delivery_content import OriginalDeliveryPlan
from lexinform.services import multipart_card_history
from lexinform.services.content_inspection import inspect_content
from tests.checkpoint_state import saved_state
from tests.scenario.test_card_edit_multipart import prepare, refresh
from tests.scenario.test_card_remote_checkpoints import ready_world


@pytest.mark.parametrize("kind", [PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL])
def test_card_original_slots_and_targets_survive_cleanup(kind: PublicationKind) -> None:
    world = ready_world("off", kind=kind, long=kind is PublicationKind.NEW_BILL, repetitions=125)
    before = len(world.publisher.sent)
    world.run(track=False)
    root = world.publication("3039", kind)
    assert root is not None and root.id is not None and root.delivery is not None
    ids = (root.id, *root.delivery.continuation_ids)
    assert len(ids) == (2 if kind is PublicationKind.NEW_BILL else 1)
    rows = [world.repo.publication_by_id(identity) for identity in ids]
    assert all(row is not None and row.message_id for row in rows)
    sent = world.publisher.sent[before:]
    assert len(sent) == len(ids)
    cutoff = world.clock.now() + timedelta(days=999)
    world.repo.prune_history(before=cutoff, memo_before=cutoff)
    sql = world.repo.dump()
    for _ in range(2):
        with saved_state(sql) as durable:
            assert durable.repo.dump() == sql
            for identity, previous in zip(ids, rows, strict=True):
                assert previous is not None, "the real publisher sent every physical slot"
                physical = durable.repo.publication_by_id(identity)
                assert physical is not None and physical.delivery is None
                assert physical.message_id == previous.message_id
                history = durable.repo.get_publication_content(identity)
                assert history is not None and history.original_delivery_evidence is not None
                evidence = history.original_delivery_evidence
                assert evidence.kind is kind
                view = inspect_content(durable.repo, physical)
                assert view is not None and view.current_id == history.content_id
                assert view.summaries[0].full_context_available is False
                snapshot = durable.repo.get_content_snapshot(history.plan_snapshot_id)
                assert snapshot is not None, "the original version retains its plan"
                frozen = OriginalDeliveryPlan.model_validate_json(snapshot.payload_json)
                assert frozen.bindings == ids
                assert [part.html for part in frozen.parts] == [message.text for message in sent]
                assert frozen.root.reply_to == sent[0].reply_to
                if kind is PublicationKind.JOINT_BILL:
                    assert evidence.joint_primary is not None
                    assert evidence.joint_comparison_application_id is not None
                    subject = durable.repo.get_content_snapshot(history.bill_context_id)
                    assert subject is not None, "the reply retains its original subject"
                    assert (
                        durable.repo.find_joint_comparison_application_id(
                            Bill.model_validate_json(subject.payload_json),
                            recorded_at=history.recorded_at,
                        )
                        == evidence.joint_comparison_application_id
                    )
                    primary = durable.repo.publication_by_id(evidence.joint_primary.publication_id)
                    assert primary is not None and primary.number == "3038"
                    assert primary.message_id == frozen.root.reply_to
                    snapshot = durable.repo.get_content_snapshot(
                        evidence.joint_primary.bill_snapshot_id
                    )
                    assert snapshot is not None, "the original primary Bill has its own snapshot"
                    assert Bill.model_validate_json(snapshot.payload_json).number == "3038"


def test_unversioned_card_edit_obscures_original_current_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(multipart_card_history, "MAX_MULTIPART_CARD_BYTES", 1)
    case = prepare("off", "many_same")
    root = case.baseline
    assert root.id is not None, "normal publication assigns the root identity"
    before = inspect_content(case.world.repo, root)
    assert before is not None and before.current_id is not None

    refresh(case.world)

    assert case.world.publisher.edited
    after = inspect_content(case.world.repo, root)
    assert after is not None and after.original_id == before.original_id
    assert after.current_id is None
    assert after.version_count == 1


@pytest.mark.parametrize("kind", [PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL])
def test_republish_retires_the_exact_original_card_root(kind: PublicationKind) -> None:
    world = ready_world("off", kind=kind, long=kind is PublicationKind.NEW_BILL, repetitions=125)
    world.run(track=False)
    root = world.publication("3039", kind)
    assert root is not None and root.id is not None
    original = world.repo.get_publication_content(root.id)
    assert original is not None and original.original_delivery_evidence is not None
    initial = world.repo.dump()
    with pytest.raises(ValueError, match="invalid_content_history"):
        world.repo.restore(initial + f"\nDELETE FROM publications WHERE id={root.id};\n")
    assert world.repo.dump() == initial

    world.command("/republish 3039")
    world.run(track=False)
    replacement = world.publication("3039", kind)
    assert replacement is not None and replacement.id != root.id
    assert world.repo.publication_by_id(root.id) is None
    assert world.repo.get_publication_content(root.id) == original

    cutoff = world.clock.now() + timedelta(days=999)
    world.repo.prune_history(before=cutoff, memo_before=cutoff)
    dump = world.repo.dump()
    for _ in range(2):
        world.repo.restore(dump)
        assert world.repo.dump() == dump
        assert world.repo.get_publication_content(root.id) == original
    if root.delivery is not None:
        for identity in root.delivery.continuation_ids:
            child = world.repo.publication_by_id(identity)
            assert child is not None
            view = inspect_content(world.repo, child)
            assert view is not None and view.original_id == original.content_id
            assert view.current_id is None
    for operation in (
        f"DELETE FROM retired_card_roots WHERE id={root.id}",
        f"UPDATE retired_card_roots SET ref='foreign' WHERE id={root.id}",
    ):
        with pytest.raises(ValueError, match="invalid_content_history"):
            world.repo.restore(dump + f"\n{operation};\n")
        assert world.repo.dump() == dump

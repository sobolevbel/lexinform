import datetime as dt
import hashlib
from typing import Literal

import pytest

from lexinform.errors import DeliveryUncertainError
from lexinform.models import ContentSnapshot, ContentVersion, Publication, PublicationStatus, Stage
from lexinform.models.content import DirectStageContentEvidence, card_bill_snapshot
from lexinform.ports import PublishResult
from lexinform.services import content_history
from tests.harness import REFERRED, World
from tests.scenario.test_commands import _commands_only
from tests.scenario.test_tracking import WITH_REPORT


@pytest.mark.parametrize("resolution", ["confirm", "dismiss"])
def test_reader_only_held_history_survives_operator_resolution(
    resolution: Literal["confirm", "dismiss"], monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World()
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    world.run()
    reading = Stage(
        stage_name="I czytanie w komisjach", stage_type="Reading", date=dt.date(2026, 9, 6)
    )
    world.set_stages("3039", REFERRED + (reading,))
    world.clock.advance(days=1)
    assert world.run().held == 1
    world.set_stages("3039", REFERRED + (reading,) + WITH_REPORT[len(REFERRED) :])
    world.clock.advance(days=1)

    def reader_fixture_only(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(content_history, "prepare_publication_content", reader_fixture_only)
    offered: list[tuple[ContentVersion, int]] = []
    original = world.publisher.publish_prepared

    def uncertain(publication: Publication) -> PublishResult:
        plan = publication.delivery
        if plan is None or not plan.held_change_ids:
            return original(publication)
        assert publication.id is not None and publication.status_change_id is not None
        stored = world.repo.publication_by_id(publication.id)
        assert stored is not None and stored.status is PublicationStatus.PENDING
        assert plan.reply_to is not None
        (anchor,) = world.repo.card_aliases(publication.channel_id, plan.reply_to)
        assert anchor.id is not None
        bill = card_bill_snapshot(plan.bill_json)
        change = ContentSnapshot(kind="change", payload_json=plan.change_json)
        semantic = ContentSnapshot(
            kind="plan",
            payload_json=plan.model_copy(
                update={"bill_json": "", "change_json": "", "rendered_text": ""}
            ).model_dump_json(),
        )
        version = ContentVersion(
            root_publication_id=publication.id,
            reason="original",
            language="ru",
            formatter_version="reader-fixture",
            bill_snapshot_id=bill.snapshot_id,
            change_snapshot_id=change.snapshot_id,
            plan_snapshot_id=semantic.snapshot_id,
            rendered_text=plan.rendered_text,
            status_change_id=publication.status_change_id,
            direct_stage_evidence=DirectStageContentEvidence(
                anchor_publication_id=anchor.id,
                original_bill_sha256=hashlib.sha256(plan.bill_json.encode()).hexdigest(),
                held="held-stage-prefix-v1",
            ),
            recorded_at=world.clock.now(),
        )
        with world.repo.atomic():
            for snapshot in (bill, change, semantic):
                world.repo.save_content_snapshot(snapshot)
            world.repo.save_content_version(version)
            world.repo.bind_publication_content(publication.id, version.content_id)
        pending = world.repo.dump()
        world.repo.restore(pending)
        assert world.repo.dump() == pending
        (held,) = plan.held_change_ids
        offered.append((version, held))
        raise DeliveryUncertainError("response lost")

    monkeypatch.setattr(world.publisher, "publish_prepared", uncertain)
    world.run()
    ((version, held),) = offered
    root = world.repo.publication_by_id(version.root_publication_id)
    assert root is not None and root.id is not None and root.status is PublicationStatus.UNKNOWN
    unknown = world.repo.dump()
    world.repo.restore(unknown)
    assert world.repo.dump() == unknown
    suffix = "confirm 777" if resolution == "confirm" else "dismiss obsolete"
    world.command(f"/delivery {root.id} {suffix}")
    _commands_only(world)
    confirmed = world.repo.publication_by_id(root.id)
    parent = world.repo.get_update_publication(held, root.channel_id)
    assert confirmed is not None and parent is not None
    expected = PublicationStatus.SENT if resolution == "confirm" else PublicationStatus.DISMISSED
    assert confirmed.status is parent.status is expected
    assert (confirmed.message_id, confirmed.sent_at) == (parent.message_id, parent.sent_at)
    saved = world.repo.dump()
    for _ in range(2):
        world.repo.restore(saved)
        assert world.repo.dump() == saved
        assert world.repo.get_publication_content(root.id) == version
    if resolution == "dismiss":
        with pytest.raises(ValueError, match="invalid_content_history"):
            world.repo.restore(
                saved + f"\nDELETE FROM delivery_resolutions WHERE publication_id={root.id};\n"
            )
        assert world.repo.dump() == saved

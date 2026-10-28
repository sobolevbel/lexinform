from collections.abc import Callable

import pytest

from lexinform.models import DeliveryPlan, Publication, PublicationKind, PublicationStatus
from lexinform.services.posting import Poster
from tests.harness import CHANNEL, World
from tests.scenario.test_card_revision_reader import row, saved_edit


def checked_poster(world: World, checkpoint: Callable[[], None]) -> Poster:
    return Poster(
        world.repo,
        world.publisher,
        world.clock,
        gateway=world.gateway,
        channel_id=CHANNEL,
        max_attempts=3,
        checkpoint=checkpoint,
    )


@pytest.mark.parametrize("dangling_root", [False, True])
def test_unclassified_physical_delivery_stops_before_pending_checkpoint_or_api(
    dangling_root: bool,
) -> None:
    world = World()
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    card = world.publication("3039")
    assert card is not None and card.id is not None and card.message_id is not None, (
        "the normal publishing path delivered the card before its saved physical child"
    )
    identity = world.repo.create_publication(
        Publication(
            term=card.term,
            number=card.number,
            kind=PublicationKind.MESSAGE_PART,
            channel_id=CHANNEL,
            status=PublicationStatus.QUEUED,
            ref="unclassified/physical-part",
            created_at=world.clock.now(),
            delivery=DeliveryPlan(
                bill_json=world.bill("3039").model_dump_json(),
                rendered_text="<b>Saved physical HTML</b>",
                reply_to=card.message_id,
                continuation_parent=card.id,
                continuation_root=999_999 if dangling_root else None,
            ),
        )
    )
    saved = row(world, identity)
    before = world.repo.dump()
    sends = len(world.publisher.sent)
    checkpoints: list[bool] = []

    def checkpoint() -> None:
        checkpoints.append(True)

    poster = checked_poster(world, checkpoint)

    with pytest.raises(ValueError, match="no supported checkpoint owner"):
        poster.deliver(saved)

    assert world.repo.dump() == before
    assert len(world.publisher.sent) == sends
    assert not checkpoints


@pytest.mark.parametrize("legacy", [False, True])
def test_recognized_saved_card_revision_keeps_both_reader_checkpoints(legacy: bool) -> None:
    case = saved_edit(legacy=legacy)
    world = case.world
    source = row(world, case.identities[0])
    assert source.delivery is not None, "saved_edit retains its frozen HTML and edit target"
    checkpoints: list[PublicationStatus] = []

    def checkpoint() -> None:
        checkpoints.append(row(world, case.identities[0]).status)

    poster = checked_poster(world, checkpoint)
    before = len(world.publisher.edited)

    assert poster.deliver(source)

    assert checkpoints == [PublicationStatus.PENDING, PublicationStatus.SENT]
    assert len(world.publisher.edited) == before + 1
    assert row(world, case.identities[0]).delivery == source.delivery

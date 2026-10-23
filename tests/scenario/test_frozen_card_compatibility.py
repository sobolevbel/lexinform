from typing import Literal

import pytest

from lexinform.models import DeliveryPlan, Publication, PublicationKind, PublicationStatus
from tests.harness import CHANNEL, TERM, World


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
@pytest.mark.parametrize("kind", [PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL])
def test_card_reader_reuses_saved_html_before_the_new_producer_is_enabled(
    mode: Literal["off", "shadow", "on"], kind: PublicationKind
) -> None:
    world = World(reader_attention=mode)
    primary = None
    reply_to = None
    if kind is PublicationKind.JOINT_BILL:
        world.add_bill("3038", "Projekt ustawy o cudzoziemcach")
        world.run()
        primary = world.bill("3038")
        reply_to = world.card_id("3038")
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    if primary is not None:
        world.touch("3039", world.clock.now(), prints_considered_jointly=("3038",))
        world.touch("3038", world.clock.now(), prints_considered_jointly=("3039",))
    world.run(max_publish=0)
    saved_html = "<b>Сохранённая карточка</b>\nНе пересобирать при повторе."
    world.repo.create_publication(
        Publication(
            term=TERM,
            number="3039",
            kind=kind,
            channel_id=CHANNEL,
            status=PublicationStatus.FAILED,
            attempts=1,
            created_at=world.clock.now(),
            delivery=DeliveryPlan(
                bill_json=world.bill("3039").model_dump_json(),
                primary_json=primary.model_dump_json() if primary is not None else None,
                reply_to=reply_to,
                rendered_text=saved_html,
                policy_version="legacy",
            ),
        )
    )

    world.run(track=False)

    assert world.publisher.texts(kind)[-1] == saved_html
    assert world.publisher.sent[-1].reply_to == reply_to
    publication = world.publication("3039", kind)
    assert publication is not None, "the saved delivery retains its identity after retry"
    assert publication.status is PublicationStatus.SENT

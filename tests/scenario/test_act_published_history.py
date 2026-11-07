from datetime import timedelta

from lexinform.models import PublicationKind
from lexinform.services.content_history import restore_content_delivery
from lexinform.services.content_inspection import inspect_content
from tests.checkpoint_state import saved_state
from tests.harness import ELI, World, act


def test_act_published_history_survives_ordinary_delivery_and_cleanup() -> None:
    world = World(profile="production", reader_attention="off")
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    world.batch.resolve()
    world.run()
    world.gateway.acts[ELI] = act()
    world.publish_act("3039")
    world.clock.advance(days=1)

    assert world.run().acts_published == 1
    pub = world.publication("3039", PublicationKind.ACT_PUBLISHED)
    assert pub is not None and pub.id is not None and pub.delivery is not None, (
        "the tracker saved the ordinary act notice"
    )
    assert pub.message_id is not None
    assert world.publisher.texts(PublicationKind.ACT_PUBLISHED) == [pub.delivery.rendered_text]
    cutoff = world.clock.now() + timedelta(days=999)
    world.repo.prune_history(before=cutoff, memo_before=cutoff)
    sql = world.repo.dump()
    for _ in range(2):
        with saved_state(sql) as durable:
            physical = durable.repo.publication_by_id(pub.id)
            assert physical is not None and physical.delivery is None
            assert physical.message_id == pub.message_id
            version = durable.repo.get_publication_content(pub.id)
            assert version is not None, "cleanup retains the act's immutable edition"
            assert restore_content_delivery(durable.repo, version) == pub.delivery
            inspected = inspect_content(durable.repo, physical)
            assert inspected is not None and inspected.current_id == version.content_id
            assert durable.repo.dump() == sql

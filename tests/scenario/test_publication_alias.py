import pytest

from lexinform.models import Publication, PublicationKind, PublicationStatus
from lexinform.services.publications import inherit_card
from tests.harness import TERM, World


@pytest.mark.parametrize("existing", [False, True])
def test_alias_updates_message_metadata_and_rolls_back_with_its_owner(existing: bool) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    card = w.publication("3039")
    assert card is not None
    channel = card.channel_id
    if existing:
        w.repo.create_publication(
            Publication(
                term=TERM,
                number="3100",
                kind=PublicationKind.NEW_BILL,
                status=PublicationStatus.FAILED,
                channel_id=channel,
                message_id=999,
                created_at=w.clock.now(),
            )
        )
    before = w.repo.get_publication(TERM, "3100", PublicationKind.NEW_BILL, channel)
    with pytest.raises(RuntimeError, match="rollback"), w.repo.atomic():
        inherit_card(
            w.repo, term=TERM, number="3100", channel_id=channel, card=card, now=w.clock.now()
        )
        raise RuntimeError("rollback")
    assert w.repo.get_publication(TERM, "3100", PublicationKind.NEW_BILL, channel) == before

    ids = []
    for _ in range(2):
        alias = inherit_card(
            w.repo, term=TERM, number="3100", channel_id=channel, card=card, now=w.clock.now()
        )
        saved = w.repo.get_publication(TERM, "3100", PublicationKind.NEW_BILL, channel)
        assert saved is not None
        assert saved.status is PublicationStatus.SENT
        assert (saved.message_id, saved.sent_at) == (card.message_id, card.sent_at)
        assert alias.id == saved.id
        ids.append(saved.id)
    assert ids[0] == ids[1]

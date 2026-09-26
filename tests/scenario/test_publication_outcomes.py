import pytest

from lexinform.models import DIGEST_NUMBER, DIGEST_TERM, PublicationKind, PublicationStatus, RunMode
from tests.harness import CHANNEL, COMMITTEE_STAGES, World


@pytest.mark.parametrize("path", ["card", "update", "digest"])
@pytest.mark.parametrize("outcome", ["success", "failure", "outage"])
def test_publication_outcome_contract(path: str, outcome: str) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    if path != "card":
        w.run()
    if path == "update":
        w.clock.advance(days=1)
        w.set_stages("3039", COMMITTEE_STAGES)
        w.touch("3039", w.clock.now())
    number = DIGEST_NUMBER if path == "digest" else "3039"
    if outcome == "failure":
        w.publisher.fail_on.add(number)
    elif outcome == "outage":
        w.publisher.outage_on.add(number)

    if path == "digest":
        w.command("/digest publish ref=2026-W36")
        w.run(mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0)
        publication = w.repo.get_publication(
            DIGEST_TERM, DIGEST_NUMBER, PublicationKind.DIGEST, CHANNEL, ref="2026-W36"
        )
    else:
        w.run()
        kind = PublicationKind.NEW_BILL if path == "card" else PublicationKind.STATUS_UPDATE
        publication = w.publication("3039", kind)

    assert publication is not None
    if outcome == "success":
        assert publication.status is PublicationStatus.SENT
        assert publication.message_id is not None
        assert publication.sent_at == w.clock.now()
        assert publication.error is None
    else:
        assert publication.status is PublicationStatus.FAILED
        assert publication.error is not None
        assert publication.sent_at is None
    assert publication.attempts == (1 if outcome == "failure" else 0)

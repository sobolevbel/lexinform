import pytest

from lexinform.errors import DeliveryUncertainError
from lexinform.models import Publication, PublicationKind, PublicationStatus
from lexinform.ports import PublishResult
from tests.harness import CHANNEL, COMMITTEE_STAGES, TERM, World
from tests.scenario.test_tracking_agenda import ASW, _sitting


def test_uncertain_sitting_group_is_recovered_whole_through_any_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = World(reader_attention="on")
    for number in ("3039", "3040"):
        w.add_bill(number, "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES)
    w.gateway.committees["ASW"] = ASW
    w.run()
    w.gateway.committee_sittings["ASW"] = (
        _sitting(agenda="Pierwsze czytanie (druki nr 3039 i 3040)"),
    )

    def uncertain(publication: Publication) -> PublishResult:
        raise DeliveryUncertainError("connection lost")

    with monkeypatch.context() as patch:
        patch.setattr(w.publisher, "publish_prepared", uncertain)
        w.run()
    pubs = [
        w.repo.get_publication(
            TERM, number, PublicationKind.AGENDA, CHANNEL, ref=w.bill(number).agenda[0].ref
        )
        for number in ("3039", "3040")
    ]
    assert all(pub is not None and pub.status is PublicationStatus.UNKNOWN for pub in pubs)
    member = pubs[1]
    assert member is not None and member.id is not None and member.delivery is not None
    frozen = member.delivery.group_text
    assert len(member.delivery.sitting_group) == 2
    w.repo.restore(w.repo.dump())
    w.run()
    assert not w.publisher.texts(PublicationKind.AGENDA)
    w.command(f"/delivery {member.id} retry")

    report = w.run()

    assert report.agenda_posted == 2
    assert w.publisher.texts(PublicationKind.AGENDA) == [frozen]
    assert report.attention["messages"] == 1
    for identity in member.delivery.sitting_group:
        pub = w.repo.publication_by_id(identity)
        assert pub is not None and pub.status is PublicationStatus.SENT

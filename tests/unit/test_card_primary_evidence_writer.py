from datetime import timedelta

import pytest

from lexinform.models import Bill, Publication, PublicationKind, PublicationStatus
from lexinform.ports import PublishResult
from lexinform.services.publishing import PublishingService
from tests.fakes import FakePublisher, FakeSejmGateway, FixedClock
from tests.unit.test_card_primary_evidence_reader import card_parents
from tests.unit.test_content_sources import Case
from tests.unit.test_content_sources import case as case
from tests.unit.test_input_receipts import NOW


@pytest.mark.parametrize("missing_collection", [False, True])
def test_original_publication_captures_exact_evidence_before_first_send(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    missing_collection: bool,
) -> None:
    fixture, reference = card_parents(case)
    assert fixture.delivery is not None, "the fixture retains its actual paid analysis"
    bill = Bill.model_validate_json(fixture.delivery.bill_json)
    case.repo.delete_publication(
        bill.term, bill.number, PublicationKind.NEW_BILL, fixture.channel_id
    )
    assert case.repo.find_card_primary_evidence(bill) == reference
    if missing_collection:
        case.execute(
            "DELETE FROM result_applications WHERE phase='collection' AND result_id=("
            "SELECT result_id FROM result_applications WHERE id=?)",
            (reference.projection_application_id,),
        )
    publisher = FakePublisher()
    send = publisher.publish_prepared

    def observe_send(publication: Publication) -> PublishResult:
        assert publication.id is not None, "the public writer saves the pending identity"
        version = case.repo.get_publication_content(publication.id)
        assert version is not None
        physical = case.repo.publication_by_id(publication.id)
        assert physical is not None and physical.status is PublicationStatus.PENDING
        assert version.card_primary_evidence == (None if missing_collection else reference)
        return send(publication)

    monkeypatch.setattr(publisher, "publish_prepared", observe_send)
    service = PublishingService(
        FakeSejmGateway(),
        case.repo,
        publisher,
        FixedClock(NOW),
        channel_id="technical",
        attention_assess=False,
    )

    assert service.publish_bill(bill)

    original = service.card_of(bill)
    assert original is not None and original.id is not None
    assert original.status is PublicationStatus.SENT
    recorded = case.repo.get_publication_content(original.id)
    assert recorded is not None and recorded.card_primary_evidence == (
        None if missing_collection else reference
    )
    assert len(case.repo.list_content_versions(original.id)) == 1
    assert original.delivery is not None
    assert len(publisher.sent) == 1
    assert recorded.rendered_text == original.delivery.rendered_text == publisher.sent[0].text
    assert service.publish_bill(bill) and len(publisher.sent) == 1
    assert case.repo.get_publication_content(original.id) == recorded
    case.repo.prune_history(before=NOW + timedelta(days=91), memo_before=NOW + timedelta(days=1))
    sql = case.repo.dump()
    case.repo.restore(sql)
    assert case.repo.dump() == sql
    assert case.repo.get_publication_content(original.id) == recorded

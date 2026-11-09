from datetime import timedelta

import pytest

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import (
    Bill,
    CommandOutcome,
    IncomingCommand,
    OutcomeStatus,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.ports import PublishResult
from lexinform.services.card_edits import edit_card
from lexinform.services.content_inspection import inspect_content_page
from lexinform.services.publishing import PublishingService
from tests.fakes import FakePublisher, FakeSejmGateway, FixedClock
from tests.unit.test_card_content_writer import renamed
from tests.unit.test_card_primary_evidence_reader import card_parents, store_marked_card
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
        pending_page = inspect_content_page(case.repo, physical, page=1)
        assert "original_primary_lineage" not in pending_page.model_dump_json()
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
    retained = case.repo.publication_by_id(original.id)
    assert retained is not None and retained.delivery is None, (
        "the original binding survives ordinary payload retention"
    )
    page = inspect_content_page(case.repo, retained, page=1)
    assert page.history is not None and page.history.current_id == recorded.content_id
    assert page.original_primary_lineage is (None if missing_collection else True)
    outcome = CommandOutcome(status=OutcomeStatus.DELIVERY, content_history_page=page)
    serialized = outcome.model_dump_json()
    assert ("original_primary_lineage" in serialized) is not missing_collection
    command = IncomingCommand(
        update_id=1,
        chat_id="technical",
        message_id=1,
        text=f"/delivery {original.id} history",
        received_at=NOW,
    )
    rendered = MessageFormatter("en").command_reply(command, outcome).text
    assert ("Original card primary lineage was retained." in rendered) is not missing_collection
    for private_value in (reference.source_observation_id, "projection_application_id"):
        assert private_value not in serialized and private_value not in rendered
    assert case.repo.dump() == sql


@pytest.mark.parametrize("changed_analysis", [False, True])
def test_first_compact_edit_marks_only_its_exact_paid_input(
    case: Case, monkeypatch: pytest.MonkeyPatch, changed_analysis: bool
) -> None:
    original, previous = store_marked_card(case)
    assert original.id is not None and original.delivery is not None, (
        "the fixture stores a bound original render"
    )
    case.repo.mark_publication(original.id, PublicationStatus.SENT, message_id=10, sent_at=NOW)
    root = case.repo.publication_by_id(original.id)
    assert root is not None, "the fixture confirms the physical root"
    bill = renamed(Bill.model_validate_json(original.delivery.bill_json))
    record = bill.analysis
    assert record is not None, "the fixture renders the original paid analysis"
    if changed_analysis:
        bill = bill.model_copy(
            update={
                "analysis": record.model_copy(
                    update={
                        "analysis": record.analysis.model_copy(update={"summary": "New analysis"})
                    }
                )
            }
        )
    publisher = FakePublisher()
    send = publisher.publish_prepared
    edited_at = NOW + timedelta(days=1)
    expected = publisher.render_card_parts(bill, None, minimum=1, today=edited_at.date())
    assert len(expected) == 1, "the fixture uses a compact edit"

    def observe_edit(publication: Publication) -> PublishResult:
        assert publication.id is not None and publication.delivery is not None, (
            "the edit is frozen before transmission"
        )
        assert publication.delivery.rendered_text == expected[0]
        version = case.repo.get_publication_content(publication.id)
        assert version is not None and version.previous_version_id == previous.content_id
        assert (version.card_primary_evidence is not None) == (not changed_analysis)
        return send(publication)

    monkeypatch.setattr(publisher, "publish_prepared", observe_edit)
    result = edit_card(
        case.repo,
        FixedClock(edited_at),
        publisher,
        bill,
        root,
        None,
        channel_id=root.channel_id,
        today=edited_at.date(),
    )
    assert result.completed
    case.repo.prune_history(
        before=edited_at + timedelta(days=1), memo_before=NOW - timedelta(days=89)
    )
    sql = case.repo.dump()
    case.repo.restore(sql)
    assert case.repo.dump() == sql


@pytest.mark.parametrize("changed_answer", [False, True])
def test_later_compact_edits_continue_only_confirmed_same_paid_input(
    case: Case, changed_answer: bool
) -> None:
    original, version = store_marked_card(case)
    assert original.id is not None and original.delivery is not None, "the original is frozen"
    case.repo.mark_publication(original.id, PublicationStatus.SENT, message_id=10, sent_at=NOW)
    bill = renamed(Bill.model_validate_json(original.delivery.bill_json))
    record = bill.analysis
    name = bill.editorial.mnemonic
    assert record is not None and name is not None, (
        "the fixture starts with a paid analysis and name"
    )
    publisher = FakePublisher()

    for index in (1, 2, 3):
        edited_at = NOW + timedelta(days=index * 2)
        root = case.repo.publication_by_id(original.id)
        assert root is not None, "the root physical identity remains the same"
        analysis = record
        if index == 2 and changed_answer:
            analysis = record.model_copy(
                update={
                    "analysis": record.analysis.model_copy(update={"summary": "Changed answer"})
                }
            )
        edition = bill.model_copy(
            update={
                "analysis": analysis,
                "editorial": bill.editorial.model_copy(
                    update={"mnemonic": name.model_copy(update={"name": f"Edition {index}"})}
                ),
            }
        )
        expected = publisher.render_card_parts(edition, None, minimum=1, today=edited_at.date())
        assert len(expected) == 1, "the normal formatter keeps this revision in one message"
        outcome = edit_card(
            case.repo,
            FixedClock(edited_at),
            publisher,
            edition,
            root,
            None,
            channel_id=root.channel_id,
            today=edited_at.date(),
        )
        assert outcome.completed
        assert (publisher.edited[-1][0].text, publisher.edited[-1][1]) == (expected[0], 10)
        versions = case.repo.list_content_versions(original.id)
        assert len(versions) == index + 1
        current = versions[-1]
        assert current.previous_version_id == version.content_id
        assert (current.card_primary_evidence is not None) == (not changed_answer or index == 1)
        case.repo.prune_history(
            before=edited_at + timedelta(days=1), memo_before=NOW - timedelta(days=89)
        )
        sql = case.repo.dump()
        case.repo.restore(sql)
        assert case.repo.dump() == sql
        version = current

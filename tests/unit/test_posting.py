"""Poster: the pending-before-send protocol for replies under a card."""

import datetime as dt

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.errors import TelegramUnavailableError
from lexinform.models import (
    AgendaItem,
    Bill,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
    StatusChange,
)
from lexinform.services.tracking.posting import Poster
from lexinform.services.tracking.result import TrackingResult
from tests.fakes import FakePublisher, FixedClock
from tests.harness import act, submission

CHANNEL = "@test"
SITTING = AgendaItem(
    kind="committee", ref="ASW/136/2026-09-17", date=dt.date(2026, 9, 17), committee_code="ASW"
)


@pytest.fixture
def bill(repo: SqliteBillRepository, process_3039: ProcessDetail, now: dt.datetime) -> Bill:
    stored = repo.upsert_summary(process_3039, now=now)
    repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.NEW_BILL,
            status=PublicationStatus.SENT,
            channel_id=CHANNEL,
            message_id=42,
            created_at=now,
        )
    )
    # These tests post every kind of reply under one card, and each is rendered for real: the
    # bill has to carry the act and the consultation window those messages are about.
    return stored.model_copy(update={"act": act(), "submission": submission(print_number="3039")})


def _poster(repo: SqliteBillRepository, publisher: FakePublisher, attempts: int = 3) -> Poster:
    return Poster(repo, publisher, FixedClock(), channel_id=CHANNEL, max_attempts=attempts)


def test_successful_post_replies_to_the_card_and_is_recorded_as_sent(
    repo: SqliteBillRepository, bill: Bill
) -> None:
    publisher = FakePublisher()

    sent = _poster(repo, publisher).act_published(bill)

    assert sent
    assert publisher.acts == [(bill, 42)]
    row = repo.get_publication(10, "3039", PublicationKind.ACT_PUBLISHED, CHANNEL)
    assert row is not None and (row.status, row.message_id) == (PublicationStatus.SENT, 101)


def test_failed_post_is_recorded_with_the_error_and_counted_as_an_attempt(
    repo: SqliteBillRepository, bill: Bill
) -> None:
    poster = _poster(repo, FakePublisher(fail_on={"3039"}))

    sent = poster.in_force(bill, today=dt.date(2026, 9, 7))

    assert not sent
    row = repo.get_publication(10, "3039", PublicationKind.IN_FORCE, CHANNEL)
    assert row is not None and (row.status, row.attempts) == (PublicationStatus.FAILED, 1)
    assert row.error is not None and "rejected" in row.error
    assert poster.posted(bill, PublicationKind.IN_FORCE)
    poster.start_run()
    assert not poster.posted(bill, PublicationKind.IN_FORCE)


def test_post_is_blocked_once_the_attempts_are_exhausted(
    repo: SqliteBillRepository, bill: Bill
) -> None:
    poster = _poster(repo, FakePublisher(fail_on={"3039"}), attempts=2)

    poster.consultation_results(bill)
    poster.consultation_results(bill)

    assert poster.posted(bill, PublicationKind.CONSULTATION_RESULTS)


def test_outage_marks_the_row_failed_and_propagates(repo: SqliteBillRepository, bill: Bill) -> None:
    poster = _poster(repo, FakePublisher(outage_on={"3039"}))

    with pytest.raises(TelegramUnavailableError):
        poster.consultation_deadline(bill, today=dt.date(2026, 9, 27))

    row = repo.get_publication(10, "3039", PublicationKind.CONSULTATION_DEADLINE, CHANNEL)
    assert row is not None and row.status is PublicationStatus.FAILED
    assert row.error is not None and "Telegram API unavailable" in row.error


def test_agenda_posts_are_tracked_per_sitting(repo: SqliteBillRepository, bill: Bill) -> None:
    poster = _poster(repo, FakePublisher())
    other = SITTING.model_copy(update={"ref": "sejm/65/2026-09-15", "kind": "sejm"})

    poster.queue_sitting(
        poster.prepare_message(bill, PublicationKind.AGENDA, ref=SITTING.ref, item=SITTING)
    )
    poster.flush_sittings(TrackingResult())

    assert poster.posted(bill, PublicationKind.AGENDA, ref=SITTING.ref)
    assert not poster.posted(bill, PublicationKind.AGENDA, ref=other.ref)


def test_the_sittings_of_a_run_go_out_as_one_post(repo: SqliteBillRepository, bill: Bill) -> None:
    publisher = FakePublisher()
    poster = _poster(repo, publisher)
    other = SITTING.model_copy(update={"ref": "sejm/65/2026-09-15", "kind": "sejm"})
    result = TrackingResult()

    for item in (SITTING, other):
        poster.queue_sitting(
            poster.prepare_message(bill, PublicationKind.AGENDA, ref=item.ref, item=item)
        )
    poster.flush_sittings(result)

    assert len(publisher.sent) == 1 and result.agenda_posted == 2
    rows = [
        repo.get_publication(10, "3039", PublicationKind.AGENDA, CHANNEL, ref=item.ref)
        for item in (SITTING, other)
    ]
    assert {row.message_id for row in rows if row is not None} == {101}


def test_a_failed_sittings_post_leaves_every_line_to_retry(
    repo: SqliteBillRepository, bill: Bill
) -> None:
    poster = _poster(repo, FakePublisher(fail_on={"3039"}))
    result = TrackingResult()

    poster.queue_sitting(
        poster.prepare_message(bill, PublicationKind.AGENDA, ref=SITTING.ref, item=SITTING)
    )
    poster.flush_sittings(result)

    row = repo.get_publication(10, "3039", PublicationKind.AGENDA, CHANNEL, ref=SITTING.ref)
    assert row is not None and (row.status, row.attempts) == (PublicationStatus.FAILED, 1)
    assert result.failed == 1


def test_long_roundup_keeps_every_sitting_and_its_application_address(
    repo: SqliteBillRepository, bill: Bill
) -> None:
    publisher = FakePublisher()
    poster = _poster(repo, publisher)
    result = TrackingResult()
    for number in range(8):
        item = SITTING.model_copy(
            update={
                "ref": f"ASW/{number}/2026-09-17",
                "committee_name": "Komisja Administracji i Spraw Wewnętrznych",
                "text": "Rozpatrzenie projektu ustawy o cudzoziemcach. " * 6,
                "condition": "first_reading_referral",
                "apply_email": f"sitting-{number}@sejm.gov.pl",
                "apply_by": dt.date(2026, 9, 16),
                "closed": True,
            }
        )
        poster.queue_sitting(
            poster.prepare_message(bill, PublicationKind.AGENDA, ref=item.ref, item=item)
        )

    poster.flush_sittings(result)

    assert result.agenda_posted == 8
    for number in range(8):
        row = repo.get_publication(
            bill.term,
            bill.number,
            PublicationKind.AGENDA,
            CHANNEL,
            ref=f"ASW/{number}/2026-09-17",
        )
        assert row is not None and row.message_id is not None
        message = publisher.sent[row.message_id - 101]
        assert f"sitting-{number}@sejm.gov.pl" in message.text


def test_a_queued_sitting_keeps_its_saved_card_link(repo: SqliteBillRepository, bill: Bill) -> None:
    publisher = FakePublisher()
    poster = _poster(repo, publisher)
    planned = poster.prepare_message(bill, PublicationKind.AGENDA, ref=SITTING.ref, item=SITTING)
    card = poster.card(bill)
    assert card is not None and card.id is not None
    repo.mark_publication(card.id, PublicationStatus.SENT, message_id=999)

    poster.queue_sitting(planned)
    poster.flush_sittings(TrackingResult())

    assert publisher.agendas[0][2] == 42


def test_an_oversized_sitting_is_failed_instead_of_partly_told(
    repo: SqliteBillRepository, bill: Bill
) -> None:
    publisher = FakePublisher()
    poster = _poster(repo, publisher)
    item = SITTING.model_copy(update={"room": "x" * 5000})
    planned = poster.prepare_message(bill, PublicationKind.AGENDA, ref=item.ref, item=item)
    result = TrackingResult()

    poster.queue_sitting(planned)
    poster.flush_sittings(result)

    assert publisher.sent == []
    assert (result.agenda_posted, result.failed) == (0, 1)
    row = repo.get_publication(
        bill.term, bill.number, PublicationKind.AGENDA, CHANNEL, ref=item.ref
    )
    assert row is not None and row.status is PublicationStatus.FAILED


def test_held_changes_go_out_with_the_next_update_and_are_released(
    repo: SqliteBillRepository, bill: Bill, now: dt.datetime
) -> None:
    publisher = FakePublisher()
    poster = _poster(repo, publisher)
    reading = Stage(stage_name="I czytanie w komisjach", stage_type="Reading")
    report = Stage(stage_name="Sprawozdanie komisji", stage_type="CommitteeReport")
    first = _change(bill, [reading], "fp1", now)
    second = _change(bill, [report], "fp2", now)
    first.id = repo.add_status_change(first)
    second.id = repo.add_status_change(second)

    poster.hold(bill, first)
    held_before = repo.list_held_status_changes(10, "3039", CHANNEL)
    sent = poster.status_update(bill, second)

    assert [c.id for c in held_before] == [first.id]
    assert sent and len(publisher.updates) == 1
    _, posted, _ = publisher.updates[0]
    assert [st.stage_type for st in posted.new_stages] == ["Reading", "CommitteeReport"]
    assert repo.list_held_status_changes(10, "3039", CHANNEL) == []
    update = repo.get_publication(10, "3039", PublicationKind.STATUS_UPDATE, CHANNEL)
    assert update is not None and update.status is PublicationStatus.SENT


def test_retry_does_not_consume_stages_held_after_the_original_plan(
    repo: SqliteBillRepository, bill: Bill, now: dt.datetime
) -> None:
    publisher = FakePublisher(fail_on={"3039"})
    poster = _poster(repo, publisher)
    report = Stage(stage_name="Sprawozdanie komisji", stage_type="CommitteeReport")
    reading = Stage(stage_name="II czytanie", stage_type="Reading")
    first = _change(bill, [report], "fp1", now)
    later = _change(bill, [reading], "fp2", now)
    first.id = repo.add_status_change(first)
    later.id = repo.add_status_change(later)
    assert not poster.status_update(bill, first)  # Telegram rejected it: the row is `failed`
    poster.hold(bill, later)  # a later run holds a service stage: a newer row than the failed one
    publisher.fail_on = set()

    sent = poster.status_update(bill, first)  # the retry keeps the old row

    assert sent
    _, posted, _ = publisher.updates[0]
    assert [st.stage_type for st in posted.new_stages] == ["CommitteeReport"]
    assert [c.id for c in repo.list_held_status_changes(10, "3039", CHANNEL)] == [later.id]


def _change(bill: Bill, stages: list[Stage], fingerprint: str, now: dt.datetime) -> StatusChange:
    return StatusChange(
        term=bill.term,
        number=bill.number,
        old_fingerprint="a",
        new_fingerprint=fingerprint,
        new_stages=stages,
        detected_at=now,
    )


def test_recorded_skip_counts_as_posted(repo: SqliteBillRepository, bill: Bill) -> None:
    poster = _poster(repo, FakePublisher())

    poster.record(bill, PublicationKind.IN_FORCE, PublicationStatus.SKIPPED)

    assert poster.posted(bill, PublicationKind.IN_FORCE)

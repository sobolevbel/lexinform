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
from tests.fakes import FakePublisher, FixedClock

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
    return stored


def _poster(repo: SqliteBillRepository, publisher: FakePublisher, attempts: int = 3) -> Poster:
    return Poster(repo, publisher, FixedClock(), channel_id=CHANNEL, max_attempts=attempts)


def test_successful_post_replies_to_the_card_and_is_recorded_as_sent(
    repo: SqliteBillRepository, bill: Bill
) -> None:
    publisher = FakePublisher()

    sent = _poster(repo, publisher).act_published(bill)

    assert sent
    assert publisher.acts == [(bill, 42)]
    row = repo.get_publication(10, "3039", "act_published", CHANNEL)
    assert row is not None and (row.status, row.message_id) == (PublicationStatus.SENT, 101)


def test_failed_post_is_recorded_with_the_error_and_counted_as_an_attempt(
    repo: SqliteBillRepository, bill: Bill
) -> None:
    poster = _poster(repo, FakePublisher(fail_on={"3039"}))

    sent = poster.in_force(bill)

    assert not sent
    row = repo.get_publication(10, "3039", "in_force", CHANNEL)
    assert row is not None and (row.status, row.attempts) == (PublicationStatus.FAILED, 1)
    assert row.error is not None and "rejected" in row.error
    assert not poster.posted(bill, PublicationKind.IN_FORCE)  # still worth retrying


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

    row = repo.get_publication(10, "3039", "consultation_deadline", CHANNEL)
    assert row is not None and row.status is PublicationStatus.FAILED
    assert row.error is not None and "Telegram API unavailable" in row.error


def test_agenda_posts_are_tracked_per_sitting(repo: SqliteBillRepository, bill: Bill) -> None:
    poster = _poster(repo, FakePublisher())
    other = SITTING.model_copy(update={"ref": "sejm/65/2026-09-15", "kind": "sejm"})

    poster.agenda(bill, SITTING)

    assert poster.posted(bill, PublicationKind.AGENDA, ref=SITTING.ref)
    assert not poster.posted(bill, PublicationKind.AGENDA, ref=other.ref)


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
    update = repo.get_publication(10, "3039", PublicationKind.STATUS_UPDATE.value, CHANNEL)
    assert update is not None and update.status is PublicationStatus.SENT


def test_held_changes_are_released_when_a_failed_update_is_retried(
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
    assert [st.stage_type for st in posted.new_stages] == ["Reading", "CommitteeReport"]
    assert repo.list_held_status_changes(10, "3039", CHANNEL) == []  # released, not re-told


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

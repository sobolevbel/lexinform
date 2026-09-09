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


def test_recorded_skip_counts_as_posted(repo: SqliteBillRepository, bill: Bill) -> None:
    poster = _poster(repo, FakePublisher())

    poster.record(bill, PublicationKind.IN_FORCE, PublicationStatus.SKIPPED)

    assert poster.posted(bill, PublicationKind.IN_FORCE)

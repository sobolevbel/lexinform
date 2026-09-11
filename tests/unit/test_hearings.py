"""Public hearings: the reminder before applications close."""

import datetime as dt

from lexinform.models import PublicationKind, Stage
from tests.harness import COMMITTEE_STAGES, World

HEARING = Stage(
    stage_name="Wysłuchanie publiczne", stage_type="PublicHearing", date=dt.date(2026, 9, 20)
)  # applications until 2026-09-10


def test_reminder_is_posted_once_three_days_before_applications_close() -> None:
    w = World()  # clock: 2026-09-07
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES + (HEARING,))

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    assert (report.hearing_reminders, again.hearing_reminders) == (1, 0)
    bill, hearing, reply_to, today = w.publisher.hearings[0]
    assert (bill.number, hearing.date, reply_to) == (
        "3039",
        dt.date(2026, 9, 20),
        w.card_id("3039"),
    )
    assert today == dt.date(2026, 9, 7)
    posted = w.publication("3039", PublicationKind.HEARING_DEADLINE)
    assert posted is not None and posted.ref == "2026-09-20"


def test_no_reminder_far_ahead_or_once_applications_closed() -> None:
    far = HEARING.model_copy(update={"date": dt.date(2026, 10, 20)})
    over = HEARING.model_copy(update={"date": dt.date(2026, 9, 15)})  # closed on 09-05
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES + (far,))
    w.add_bill("3040", "Projekt ustawy o obywatelstwie", stages=COMMITTEE_STAGES + (over,))

    report = w.run()

    assert report.hearing_reminders == 0 and w.publisher.hearings == []


def test_failed_reminder_is_retried_next_run() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES + (HEARING,))
    w.run()  # card posted, reminder attempted in the same run
    assert len(w.publisher.hearings) == 1
    w.publisher.hearings.clear()
    w.repo.delete_publication(10, "3039", PublicationKind.HEARING_DEADLINE, "@test")
    w.publisher.fail_on = {"3039"}
    w.clock.advance(days=1)

    failed = w.run()
    w.publisher.fail_on = set()
    w.clock.advance(days=1)
    retried = w.run()

    assert (failed.hearing_reminders, retried.hearing_reminders) == (0, 1)
    assert len(w.publisher.hearings) == 1

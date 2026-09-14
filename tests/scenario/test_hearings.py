"""Public hearings: the reminder before applications close."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import PublicationKind, PublicationStatus, Stage
from tests.fakes import make_analysis
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


def test_no_reminder_while_the_deadline_is_still_far_ahead() -> None:
    far = HEARING.model_copy(update={"date": dt.date(2026, 10, 20)})
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES + (far,))

    report = w.run()

    assert report.hearing_reminders == 0 and w.publisher.hearings == []


def test_a_hearing_announced_late_is_still_told_even_though_applications_closed() -> None:
    """art. 70a asks for 14 days' notice and art. 70b for applications 10 days ahead: announced
    in between, the window never opens, and silence is the one thing that helps nobody."""
    late = HEARING.model_copy(update={"date": dt.date(2026, 9, 15)})  # applications closed 09-05
    w = World()
    w.add_bill("3040", "Projekt ustawy o obywatelstwie", stages=COMMITTEE_STAGES + (late,))

    report = w.run()

    assert report.hearing_reminders == 1
    bill, hearing, _, today = w.publisher.hearings[0]
    text = MessageFormatter("ru").hearing_deadline(bill, hearing, today=today).text
    assert "слушания</b> 15.09.2026 · приём заявок на участие закрыт" in text


def test_a_hearing_that_has_taken_place_is_not_reminded() -> None:
    over = HEARING.model_copy(update={"date": dt.date(2026, 9, 1)})
    w = World()
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


def test_a_bill_the_model_rejected_announces_no_hearing() -> None:
    """A reminder is a reply, and a bill with no card has nothing to reply under: it is not
    followed at all, so the hearing on its stage list reaches nobody. Worth pinning, because the
    hearing is the one thing on that list a reader could have acted on."""
    w = World(llm_script={"3039": make_analysis(relevant=False, score=1)})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES + (HEARING,))

    report = w.run()

    assert report.hearing_reminders == 0 and w.publisher.hearings == []
    assert w.publication("3039", PublicationKind.HEARING_DEADLINE) is None


def test_a_hearing_on_jointly_considered_prints_is_told_once_for_the_group() -> None:
    """Two prints that each got a card of their own, joined by the Sejm afterwards and sent to
    one hearing. The reader wants to know about the hearing, not about the filing history, so the
    second print records the reminder as skipped instead of repeating it."""
    w = World()
    w.add_bill("3039", "Poselski projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES)
    w.add_bill("3050", "Rządowy projekt ustawy o obywatelstwie", stages=COMMITTEE_STAGES)
    w.run()  # two separate cards: nothing yet says they are one subject
    for number, other in (("3039", "3050"), ("3050", "3039")):
        w.set_stages(number, COMMITTEE_STAGES + (HEARING,))
        w.touch(
            number, dt.datetime(2026, 9, 7, 5, tzinfo=dt.UTC), prints_considered_jointly=(other,)
        )
    announced = w.run()  # the reminders read the bills as they were before this run stored them

    report = w.run()

    assert (announced.hearing_reminders, report.hearing_reminders) == (0, 1)
    assert [bill.number for bill, _, _, _ in w.publisher.hearings] == ["3039"]
    once = w.publication("3050", PublicationKind.HEARING_DEADLINE)
    assert once is not None and once.status is PublicationStatus.SKIPPED

"""The Senate's 30 days and the President's 21: the reader's last two windows."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import PublicationKind, PublicationStatus, Stage
from tests.harness import COMMITTEE_STAGES, World

# As the API shows a bill the Sejm has just passed: "Uchwalono" is appended at the third reading
# and stays last while the Senate has not answered.
THIRD_READING = Stage(
    stage_name="III czytanie na posiedzeniu Sejmu",
    stage_type="SejmReading",
    date=dt.date(2026, 9, 4),
    decision="uchwalono",
)
END = Stage(stage_name="Uchwalono", stage_type="End")
PASSED = (*COMMITTEE_STAGES, THIRD_READING, END)
TO_PRESIDENT = Stage(
    stage_name="Ustawę przekazano Prezydentowi do podpisu",
    stage_type="ToPresident",
    date=dt.date(2026, 9, 20),
)


def _passed() -> World:
    """A followed bill the Sejm passed on 2026-09-04: the Senate's term runs to 2026-10-04."""
    w = World()  # clock: 2026-09-07
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES)
    w.run()
    w.set_stages("3039", PASSED)
    w.touch("3039", dt.datetime(2026, 9, 6, 9, tzinfo=dt.UTC), passed=True)
    w.run()
    return w


def test_no_reminder_while_the_senate_still_has_weeks() -> None:
    w = _passed()

    report = w.run()

    assert report.decision_reminders == 0
    assert w.publisher.decision_deadlines == []


def test_the_senate_term_is_told_once_before_it_runs_out() -> None:
    w = _passed()
    w.clock.advance(days=21)  # 2026-09-28: seven days to 2026-10-04

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    assert report.decision_reminders == 1
    assert again.decision_reminders == 0
    bill, phase, reply_to, today = w.publisher.decision_deadlines[0]
    assert (bill.number, phase.key, reply_to) == ("3039", "senate", w.card_id("3039"))
    assert (phase.deadline, today) == (dt.date(2026, 10, 4), dt.date(2026, 9, 28))
    assert len(w.publisher.decision_deadlines) == 1


def test_the_president_gets_a_reminder_of_his_own() -> None:
    """One row per phase, so the Senate's term and the President's are each told once."""
    w = _passed()
    w.clock.advance(days=21)
    w.run()  # the Senate's reminder
    w.set_stages("3039", (*PASSED[:-1], TO_PRESIDENT, END))  # "Uchwalono" stays last
    w.touch("3039", dt.datetime(2026, 9, 29, 9, tzinfo=dt.UTC))
    w.clock.advance(days=7)  # 2026-10-05: the President's term runs to 2026-10-11

    report = w.run()

    assert report.decision_reminders == 1
    _, phase, _, _ = w.publisher.decision_deadlines[-1]
    assert (phase.key, phase.deadline) == ("president", dt.date(2026, 10, 11))
    rows = [
        w.repo.get_publication(10, "3039", PublicationKind.DECISION_DEADLINE, "@test", ref=ref)
        for ref in ("senate", "president")
    ]
    assert all(r is not None and r.status is PublicationStatus.SENT for r in rows)


def test_a_deadline_already_behind_us_invites_nothing() -> None:
    w = _passed()
    w.clock.advance(days=30)  # 2026-10-07: the Senate's 30 days ran out on the 4th

    report = w.run()

    assert report.decision_reminders == 0


def test_the_reminder_says_what_the_senate_decides_and_how_exact_the_date_is() -> None:
    w = _passed()
    w.clock.advance(days=21)
    w.run()
    bill, phase, _, today = w.publisher.decision_deadlines[0]

    text = MessageFormatter("ru").decision_deadline(bill, phase, today=today).text

    assert text.startswith("⏳ <b>Закон в Сенате — druk nr 3039</b>")
    assert "Сенат должен решить до 04.10.2026" in text
    assert "осталось дней: 6" in text
    # The date is counted from the Sejm's vote, not from the hand-over the API does not give.
    assert "фактический на несколько дней позже" in text
    assert "направить мнение в профильную комиссию Сената" in text
    assert "#сенат #важность5 #легализация #kadencja10druk3039" in text

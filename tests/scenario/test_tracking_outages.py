"""Telegram falling over in the middle of the tracking phase, watcher by watcher.

The publishing phase has its own outage tests (`test_outages.py`); this is the phase after it,
where six watchers each post something of their own and each has to stop the same way — the
error named in the report, the counter not spent, and the post made on the next run. The reminders
are the ones that matter most: they carry a deadline behind them, and the next run is twelve hours
away, twenty-four at a weekend.
"""

import datetime as dt

from lexinform.models import PublicationKind, PublicationStatus
from tests.harness import (
    COMMITTEE_STAGES,
    ELI,
    RCL,
    REFERRED,
    RPW,
    WYKAZ,
    World,
    act,
    rcl_project,
    submission,
)
from tests.scenario.test_hearings import HEARING


def _down(w: World, *numbers: str) -> None:
    """Telegram answered the card and is gone by the time the reply goes out."""
    w.publisher.outage_on = set(numbers)


def test_telegram_down_while_a_stage_update_goes_out_leaves_it_for_the_next_run() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)
    w.touch("3039", dt.datetime(2026, 9, 8, 9, tzinfo=dt.UTC))
    _down(w, "3039")

    failed = w.run()
    _down(w)
    w.clock.advance(days=1)
    retried = w.run()

    assert failed.updates == 0 and any("tracking: Telegram" in e for e in failed.errors)
    assert retried.updates == 1
    assert [c.number for _, c, _ in w.publisher.updates] == ["3039"]


def test_telegram_down_before_the_in_force_reminder_leaves_it_for_the_next_run() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.publish_act("3039")
    w.gateway.acts[ELI] = act()  # in force 2026-09-20
    w.clock.advance(days=2)
    w.run()  # the Dziennik Ustaw notice
    w.clock.current = dt.datetime(2026, 9, 20, 6, 0, tzinfo=dt.UTC)
    _down(w, "3039")

    failed = w.run()
    _down(w)
    w.clock.advance(days=1)
    late = w.run()

    assert failed.in_force_posted == 0 and any("tracking: Telegram" in e for e in failed.errors)
    # A day late is still the reply the reader is waiting for; the act is in force either way.
    assert late.in_force_posted == 1 and len(w.publisher.in_force) == 1


def test_telegram_down_before_the_consultation_reminder_leaves_it_for_the_next_run() -> None:
    w = World()  # clock: 2026-09-07
    w.gateway.submissions.append(submission(consultation_end=dt.date(2026, 9, 20)))
    w.run()
    w.clock.advance(days=10)  # 2026-09-17: three days left
    _down(w, RPW)

    failed = w.run()
    _down(w)
    w.run()

    assert failed.consultation_reminders == 0
    assert any("tracking: Telegram" in e for e in failed.errors)
    assert len(w.publisher.consultations) == 1


def test_telegram_down_before_the_hearing_reminder_leaves_it_for_the_next_run() -> None:
    """art. 70b gives ten days to apply, so a lost reminder is a window the reader loses."""
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES + (HEARING,))
    w.run()  # the card and, in the same run, the reminder
    w.publisher.hearings.clear()
    w.repo.delete_publication(10, "3039", PublicationKind.HEARING_DEADLINE, "@test")
    _down(w, "3039")
    w.clock.advance(days=1)

    failed = w.run()
    _down(w)
    w.clock.advance(days=1)
    retried = w.run()

    assert failed.hearing_reminders == 0
    assert any("tracking: Telegram" in e for e in failed.errors)
    assert retried.hearing_reminders == 1
    assert len(w.publisher.hearings) == 1


def test_telegram_down_while_the_register_reports_a_dropped_plan_stops_that_watcher() -> None:
    w = World()
    w.add_wykaz_entry()
    w.run()
    w.add_wykaz_entry(status="Wycofany", resignation="Nowa koncepcja.")
    _down(w, WYKAZ)

    failed = w.run()
    _down(w)
    w.clock.advance(days=1)
    retried = w.run()

    assert failed.updates == 0 and any("tracking: Telegram" in e for e in failed.errors)
    # The change row is already there; what the retry needs is the post, and the `failed` row is
    # what keeps the bill on the list until it goes out.
    assert retried.updates == 1
    posted = w.publication(WYKAZ, PublicationKind.STATUS_UPDATE)
    assert posted is not None and posted.status is PublicationStatus.SENT
    assert len(w.publisher.updates) == 1


def test_a_plan_whose_project_cannot_be_read_is_linked_on_a_later_run() -> None:
    """The project on RCL takes over the plan's thread. RCL takes ten seconds a page and is
    unreachable from a GitHub runner altogether, so a link that cannot be made now must simply
    wait — the plan must not be left half-linked, and the watcher must not take the run down."""
    w = World()
    w.add_wykaz_entry()
    w.run()
    w.add_rcl_project(rcl_project(wykaz_number="UD408"))
    # Discovery only stamps the plan with the project's id, from the list page; the linker is
    # what reads the timeline, and that is the request refused here.
    w.rcl.outages.add("get_project")

    failed = w.run()
    half_made = w.repo.get(10, RCL)  # before the retry hides what the failed run left
    w.rcl.outages.clear()
    w.clock.advance(days=1)
    linked = w.run()

    assert failed.linked == 0 and half_made is None
    assert linked.linked == 1
    assert w.bill(RCL).linked_number == WYKAZ
    assert w.publication(RCL) is not None  # the project inherited the plan's card, as ever

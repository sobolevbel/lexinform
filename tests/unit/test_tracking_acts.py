"""After the Sejm: the Dziennik Ustaw notice and the entry-into-force reminder."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import PublicationKind, PublicationStatus
from tests.harness import ELI, World, act


def _published_bill() -> World:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.publish_act("3039")
    return w


def test_eli_on_the_process_waits_for_the_eli_api() -> None:
    w = _published_bill()
    w.clock.advance(days=2)  # the ELI API lags behind the process listing

    report = w.run()

    assert report.acts_published == 0 and not report.errors
    assert w.bill("3039").act is None


def test_act_is_announced_once_when_the_eli_api_has_it() -> None:
    w = _published_bill()
    w.gateway.acts[ELI] = act()
    w.clock.advance(days=2)

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    assert (report.acts_published, report.in_force_posted) == (1, 0)
    bill, reply_to = w.publisher.acts[0]
    assert reply_to == w.card_id("3039")
    assert bill.act is not None and bill.act.eli == ELI
    assert again.acts_published == 0 and len(w.publisher.acts) == 1


def test_act_notice_renders_the_journal_address_and_the_date() -> None:
    w = _published_bill()
    w.gateway.acts[ELI] = act()
    w.clock.advance(days=2)
    w.run()

    text = MessageFormatter("ru").act_published(w.publisher.acts[0][0]).text

    assert "Опубликован в Dziennik Ustaw — druk nr 3039" in text
    assert "Dz.U. 2026 poz. 1099 (опубликован 09.09.2026)" in text
    assert "Вступает в силу:</b> 20.09.2026" in text
    assert "#закон #kadencja10druk3039" in text


def test_in_force_reminder_is_posted_on_the_day_in_warsaw_time() -> None:
    w = _published_bill()
    w.gateway.acts[ELI] = act()  # in force 2026-09-20
    w.clock.advance(days=2)
    w.run()

    w.clock.current = dt.datetime(2026, 9, 19, 6, 0, tzinfo=dt.UTC)
    eve = w.run()
    w.clock.current = dt.datetime(2026, 9, 20, 6, 0, tzinfo=dt.UTC)
    day = w.run()
    w.clock.advance(days=1)
    after = w.run()

    assert eve.in_force_posted == 0
    assert day.in_force_posted == 1
    bill, reply_to = w.publisher.in_force[0]
    assert reply_to == w.card_id("3039")
    text = MessageFormatter("ru").in_force(bill).text
    assert "С сегодняшнего дня действует — druk nr 3039" in text
    assert "Суть закона" in text and "#вступилвсилу #kadencja10druk3039" in text
    assert after.in_force_posted == 0 and len(w.publisher.in_force) == 1


def test_act_discovered_already_in_force_gets_no_separate_reminder() -> None:
    w = _published_bill()
    w.clock.current = dt.datetime(2026, 10, 1, 6, 0, tzinfo=dt.UTC)
    w.gateway.acts[ELI] = act(fetched_at=w.clock.current, in_force="IN_FORCE")

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    assert (report.acts_published, report.in_force_posted) == (1, 0)
    bill, _ = w.publisher.acts[0]
    assert "Уже действует с</b> 20.09.2026" in MessageFormatter("ru").act_published(bill).text
    assert w.publisher.in_force == []
    reminder = w.publication("3039", PublicationKind.IN_FORCE)
    assert reminder is not None and reminder.status is PublicationStatus.SKIPPED
    assert again.in_force_posted == 0


def test_failed_act_notice_is_retried_next_run() -> None:
    w = _published_bill()
    w.gateway.acts[ELI] = act()
    w.publisher.fail_on = {"3039"}
    w.clock.advance(days=2)

    failed = w.run()
    w.publisher.fail_on = set()
    w.clock.advance(days=1)
    retried = w.run()

    assert failed.acts_published == 0 and failed.errors
    assert retried.acts_published == 1 and len(w.publisher.acts) == 1

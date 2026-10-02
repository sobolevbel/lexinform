"""After the Sejm: the Dziennik Ustaw notice and the entry-into-force reminder."""

import datetime as dt

import pytest

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import Publication, PublicationKind, PublicationStatus
from tests.harness import COMMITTEE_STAGES, ELI, World, act


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
    assert "Dz.U. 2026 poz. 1099 (опубликован 9 сент 2026)" in text
    assert "Вступает в силу:</b> 20 сент 2026" in text
    assert "#закон #важность5 #легализация #kadencja10druk3039" in text


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
    assert bill.act is not None, "an in-force notice goes out only for a bill with its act"
    text = MessageFormatter("ru").in_force(bill, today=bill.act.entry_into_force).text
    assert "С сегодняшнего дня действует — druk nr 3039" in text
    assert (
        "Суть закона" in text
        and "#вступилвсилу #важность5 #легализация #kadencja10druk3039" in text
    )
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
    assert "Уже действует с</b> 20 сент 2026" in MessageFormatter("ru").act_published(bill).text
    assert w.publisher.in_force == []
    reminder = w.publication("3039", PublicationKind.IN_FORCE)
    assert reminder is not None and reminder.status is PublicationStatus.SKIPPED
    assert again.in_force_posted == 0


def test_a_stage_change_and_the_act_in_one_run_are_one_message() -> None:
    """Druki 2667 and 2699 each got an update and a Dziennik Ustaw notice a few seconds apart."""
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)
    w.publish_act("3039")
    w.gateway.acts[ELI] = act()
    w.clock.advance(days=2)

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    assert (report.updates, report.acts_published) == (1, 1)
    assert w.publisher.acts == [] and len(w.publisher.updates) == 1
    assert "Опубликован в Dziennik Ustaw" in w.publisher.texts(PublicationKind.STATUS_UPDATE)[0]
    pdf = act().text_pdf_url
    assert pdf is not None and pdf in w.publisher.texts(PublicationKind.STATUS_UPDATE)[0]
    notice = w.publication("3039", PublicationKind.ACT_PUBLISHED)
    update = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert notice is not None and update is not None
    assert notice.status is PublicationStatus.SENT and notice.message_id == update.message_id
    assert (again.updates, again.acts_published) == (0, 0)


def test_a_failed_update_carrying_the_act_is_retried_with_it_and_the_act_not_told_twice() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)
    w.publish_act("3039")
    w.gateway.acts[ELI] = act()
    w.publisher.fail_on = {"3039"}
    w.clock.advance(days=2)

    failed = w.run()
    w.publisher.fail_on = set()
    w.clock.advance(days=1)
    retried = w.run()

    assert failed.acts_published == 0
    assert w.publication("3039", PublicationKind.ACT_PUBLISHED) is not None
    assert w.publisher.acts == [] and len(w.publisher.updates) == 1
    assert "Опубликован в Dziennik Ustaw" in w.publisher.texts(PublicationKind.STATUS_UPDATE)[0]
    assert retried.acts_published == 1


@pytest.mark.parametrize("action", ["confirm 777", "dismiss already handled", "retry"])
def test_an_uncertain_combined_update_owns_the_act_until_resolved(action: str) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)
    w.publish_act("3039")
    w.gateway.acts[ELI] = act()
    w.publisher.fail_on.add("3039")
    w.run()
    update = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert update is not None and update.id is not None
    w.repo.mark_publication(update.id, PublicationStatus.PENDING, count_attempt=False)
    w.publisher.fail_on.clear()
    w.repo.restore(w.repo.dump())

    w.run()

    assert w.publisher.acts == []
    w.command(f"/delivery {update.id} {action}")
    w.run()
    w.run()

    assert w.publisher.acts == []
    notice = w.publication("3039", PublicationKind.ACT_PUBLISHED)
    if action.startswith("dismiss"):
        assert notice is None and w.publisher.updates == []
    else:
        resolved = w.repo.publication_by_id(update.id)
        assert resolved is not None and notice is not None
        assert resolved.message_id == notice.message_id
        assert notice.status is PublicationStatus.SENT


def test_combined_delivery_is_committed_atomically(monkeypatch: pytest.MonkeyPatch) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", COMMITTEE_STAGES)
    w.publish_act("3039")
    w.gateway.acts[ELI] = act()
    original = w.repo.create_publication

    def fail_act(publication: Publication) -> int:
        if publication.kind is PublicationKind.ACT_PUBLISHED:
            raise RuntimeError("act bookkeeping interrupted")
        return original(publication)

    with monkeypatch.context() as patch:
        patch.setattr(w.repo, "create_publication", fail_act)
        w.run()

    update = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert update is not None and update.status is PublicationStatus.PENDING
    assert len(w.publisher.updates) == 1
    w.run()
    assert len(w.publisher.updates) == 1 and w.publisher.acts == []


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


def test_an_act_fetched_after_midnight_in_warsaw_counts_as_already_in_force() -> None:
    """22:00 UTC is already the next day in Warsaw, and the act's dates are Polish legal dates."""
    w = _published_bill()
    w.clock.current = dt.datetime(2026, 9, 19, 23, 30, tzinfo=dt.UTC)  # 01:30 on the 20th
    w.gateway.acts[ELI] = act(fetched_at=w.clock.current)  # in force 2026-09-20

    report = w.run()

    assert (report.acts_published, report.in_force_posted) == (1, 0)
    bill, _ = w.publisher.acts[0]
    assert "Уже действует с</b> 20 сент 2026" in MessageFormatter("ru").act_published(bill).text

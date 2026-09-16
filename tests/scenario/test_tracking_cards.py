"""A card that is still live is kept true; a finished one says so once and is then left."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import Stage
from tests.harness import COMMITTEE_STAGES, REFERRED, World, act


def _followed_bill() -> World:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.run()
    return w


def test_a_run_that_changes_nothing_does_not_touch_the_card() -> None:
    w = _followed_bill()

    w.clock.advance(days=1)
    report = w.run()

    assert report.cards_refreshed == 0
    assert w.publisher.edits == []


def test_a_card_is_re_rendered_when_what_it_says_has_drifted() -> None:
    w = _followed_bill()
    card = w.card_id("3039")
    before = MessageFormatter("ru").new_bill(w.bill("3039"), None).text

    w.clock.advance(days=1)
    w.set_stages("3039", COMMITTEE_STAGES)
    w.touch("3039", dt.datetime(2026, 9, 8, 9, tzinfo=dt.UTC))
    report = w.run()

    assert report.cards_refreshed == 1
    edited, message_id = w.publisher.edits[0]
    assert (edited.number, message_id) == ("3039", card)
    after = MessageFormatter("ru").new_bill(w.bill("3039"), None).text
    assert after != before


def test_the_same_drift_is_not_edited_twice() -> None:
    w = _followed_bill()
    w.clock.advance(days=1)
    w.set_stages("3039", COMMITTEE_STAGES)
    w.touch("3039", dt.datetime(2026, 9, 8, 9, tzinfo=dt.UTC))
    w.run()

    w.clock.advance(days=1)
    again = w.run()

    assert again.cards_refreshed == 0
    assert len(w.publisher.edits) == 1


def test_a_finished_bill_says_so_on_its_card_and_is_then_left_alone() -> None:
    """The road is over, and the card is the message a reader comes back to. It used to keep the
    last live text it had — «дальше: III чтение», «можно сделать: написать в комиссию» — over a
    bill nobody was working on any more, because the refresher stopped one run too early."""
    w = _followed_bill()
    w.clock.advance(days=1)
    w.repo.save_act(w.bill("3039").term, "3039", act(entry_into_force=dt.date(2026, 9, 1)))
    w.set_stages("3039", (*COMMITTEE_STAGES, Stage(stage_type="End", stage_name="Uchwalono")))
    w.touch("3039", dt.datetime(2026, 9, 8, 9, tzinfo=dt.UTC))

    report = w.run()

    assert report.cards_refreshed == 1
    closed, _ = w.publisher.edited[-1]
    assert "Законопроект: процесс завершён" in closed.text
    assert "Уже действует с</b> 01.09.2026" in closed.text
    assert "Что можно сделать сейчас:</b> закон уже применяется" in closed.text


def test_the_finished_card_is_not_edited_again() -> None:
    """ "Left alone" is the digest's work, not a rule of its own: a finished card is stable, so
    every later run is a pure render and no request."""
    w = _followed_bill()
    w.clock.advance(days=1)
    w.repo.save_act(w.bill("3039").term, "3039", act(entry_into_force=dt.date(2026, 9, 1)))
    w.set_stages("3039", (*COMMITTEE_STAGES, Stage(stage_type="End", stage_name="Uchwalono")))
    w.touch("3039", dt.datetime(2026, 9, 8, 9, tzinfo=dt.UTC))
    w.run()

    w.clock.advance(days=1)
    again = w.run()

    assert again.cards_refreshed == 0
    assert len(w.publisher.edits) == 1


def test_telegram_going_down_on_a_refresh_does_not_erase_what_the_phase_did() -> None:
    """The refresh is the last, cosmetic step; an outage there used to throw away the phase's
    result object, so the run reported neither the update it had posted nor the tokens it spent."""
    w = _followed_bill()
    w.clock.advance(days=1)
    w.set_stages("3039", COMMITTEE_STAGES)
    w.touch("3039", dt.datetime(2026, 9, 8, 9, tzinfo=dt.UTC))
    w.publisher.outage_on_edit.add("3039")

    report = w.run()

    assert report.updates == 1 and report.tracked == 1
    assert any("tracking: Telegram" in e for e in report.errors)


def test_the_day_a_card_is_judged_by_is_the_readers_day_not_the_runners() -> None:
    """Between 22:00 and midnight UTC it is already tomorrow in Warsaw. An act that enters into
    force on the 20th is in force for the reader while the runner's clock still says the 19th."""
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.run()
    w.clock.current = dt.datetime(2026, 9, 19, 23, 30, tzinfo=dt.UTC)  # 01:30 on the 20th in Warsaw
    w.repo.save_act(10, "3039", act(entry_into_force=dt.date(2026, 9, 20)))
    w.set_stages("3039", (*COMMITTEE_STAGES, Stage(stage_type="End", stage_name="Uchwalono")))
    w.touch("3039", dt.datetime(2026, 9, 19, 9, tzinfo=dt.UTC))

    w.run()

    closed, _ = w.publisher.edited[-1]
    assert "Уже действует с</b> 20.09.2026" in closed.text
    assert "вступление в силу" not in closed.text


def test_an_act_with_a_vacatio_legis_still_ahead_keeps_its_card_true() -> None:
    """Dz.U. is not the end of the road: druk 2699 was promulgated on 2026-08-18 and enters into
    force on 2026-11-19. Until it does, «вступает в силу 19.11.2026» is what the card is for."""
    w = _followed_bill()
    w.clock.advance(days=1)
    w.repo.save_act(10, "3039", act(entry_into_force=dt.date(2026, 11, 19)))
    w.set_stages("3039", (*COMMITTEE_STAGES, Stage(stage_type="End", stage_name="Uchwalono")))
    w.touch("3039", dt.datetime(2026, 9, 8, 9, tzinfo=dt.UTC))

    report = w.run()

    assert report.cards_refreshed == 1
    edited, _ = w.publisher.edits[0]
    assert "вступление в силу 19.11.2026" in MessageFormatter("ru").new_bill(edited, None).text

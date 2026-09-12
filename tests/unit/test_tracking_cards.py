"""A card that is still live is kept true; a finished one is left as it is."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import Stage
from tests.harness import COMMITTEE_STAGES, REFERRED, World, act


def _followed() -> World:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.run()
    return w


def test_a_run_that_changes_nothing_does_not_touch_the_card() -> None:
    w = _followed()

    w.clock.advance(days=1)
    report = w.run()

    assert report.cards_refreshed == 0
    assert w.publisher.edits == []


def test_a_card_is_re_rendered_when_what_it_says_has_drifted() -> None:
    w = _followed()
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
    w = _followed()
    w.clock.advance(days=1)
    w.set_stages("3039", COMMITTEE_STAGES)
    w.touch("3039", dt.datetime(2026, 9, 8, 9, tzinfo=dt.UTC))
    w.run()

    w.clock.advance(days=1)
    again = w.run()

    assert again.cards_refreshed == 0
    assert len(w.publisher.edits) == 1


def test_a_finished_bill_keeps_the_card_it_had() -> None:
    """Its road is over: the card invites nothing, and the replies tell how it ended."""
    w = _followed()
    w.clock.advance(days=1)
    w.repo.save_act(w.bill("3039").term, "3039", act(entry_into_force=dt.date(2026, 9, 1)))
    w.set_stages("3039", (*COMMITTEE_STAGES, Stage(stage_type="End", stage_name="Uchwalono")))
    w.touch("3039", dt.datetime(2026, 9, 8, 9, tzinfo=dt.UTC))

    report = w.run()

    assert report.cards_refreshed == 0
    assert w.publisher.edits == []

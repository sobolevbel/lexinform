import datetime as dt

import pytest

from lexinform.adapters.telegram_format import MessageFormatter, length
from lexinform.models import PublicationKind
from tests.harness import World


@pytest.mark.parametrize("language", ["ru", "en"])
def test_card_puts_audience_consequence_and_action_before_details(language: str) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    bill = w.bill("3039")
    assert bill.analysis is not None
    analysis = bill.analysis.analysis
    text = MessageFormatter(language).new_bill(bill, None, today=dt.date(2026, 9, 7)).text

    assert text.index(analysis.affected_groups[0]) < text.index(analysis.practical_impact)
    assert analysis.summary not in text
    assert text.index(analysis.practical_impact) < text.index(analysis.key_changes[0])
    assert length(text) <= 4096


def test_reply_without_mnemonic_uses_saved_context() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", ())
    bill = w.bill("3039")
    assert bill.analysis is not None
    w.publish_act("3039")
    w.run()

    notices = w.publisher.texts(PublicationKind.ACT_PUBLISHED) + w.publisher.texts(
        PublicationKind.STATUS_UPDATE
    )
    assert notices and bill.analysis.analysis.summary.split(". ")[0] in notices[-1]

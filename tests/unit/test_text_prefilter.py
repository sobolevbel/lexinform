"""The second prefilter stage: keyword search inside the print PDF when the title said nothing."""

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import BillStatus
from tests.fakes import FakeTextExtractor
from tests.harness import World

FOREIGNER_TEXT = (
    "Art. 1. W ustawie o cudzoziemcach wprowadza się zmiany. "
    "Art. 2. Zezwolenie na pobyt czasowy wydaje wojewoda. "
    "Art. 3. Cudzoziemiec składa wniosek osobiście. " * 3
)


def test_bill_with_a_neutral_title_is_caught_by_its_text() -> None:
    w = World(extractor=FakeTextExtractor(FOREIGNER_TEXT))
    w.add_bill("4100", "Rządowy projekt ustawy o zmianie niektórych ustaw w związku z cyfryzacją")

    report = w.run()

    assert report.prefilter_hits == 0  # the title said nothing
    assert (report.text_prefilter_checked, report.text_prefilter_hits) == (1, 1)
    assert (report.analyzed, report.published) == (1, 1)
    bill = w.bill("4100")
    assert bill.status is BillStatus.ANALYZED
    assert bill.prefilter_hits == [
        "text:cudzoziemcy",
        "text:zezwolenie_pobyt",
        "text:pobyt_kwalifikowany",
    ]


def test_card_says_the_bill_was_found_by_its_text() -> None:
    w = World(extractor=FakeTextExtractor(FOREIGNER_TEXT))
    w.add_bill("4100", "Rządowy projekt ustawy o zmianie niektórych ustaw")
    w.run()

    text = MessageFormatter("ru").new_bill(w.bill("4100"), None).text

    assert "Найден по тексту проекта" in text


def test_pdf_is_downloaded_once_for_prefilter_and_analysis() -> None:
    w = World(extractor=FakeTextExtractor(FOREIGNER_TEXT))
    w.add_bill("4100", "Rządowy projekt ustawy o zmianie niektórych ustaw")

    w.run()

    assert sum(1 for c in w.gateway.calls if c.startswith("download:")) == 1


def test_single_stray_mention_is_rejected_but_kept_for_tuning() -> None:
    w = World(extractor=FakeTextExtractor("Art. 1. " * 40 + "cudzoziemiec " + "Art. 2. " * 40))
    w.add_bill("4100", "Rządowy projekt ustawy o podatku")

    report = w.run()

    assert (report.text_prefilter_hits, report.analyzed) == (0, 0)
    bill = w.bill("4100")
    assert bill.status is BillStatus.SKIPPED_TEXT_PREFILTER
    assert bill.prefilter_hits == ["text:cudzoziemcy"]


def test_missing_or_broken_pdf_skips_the_bill_quietly() -> None:
    w = World(extractor=FakeTextExtractor(error=ValueError("not a PDF")))
    w.add_bill("4100", "Rządowy projekt ustawy o podatku")
    w.add_bill("4101", "Rządowy projekt ustawy o lasach", with_pdf=False)

    report = w.run()

    assert report.text_prefilter_checked == 2 and not report.errors
    assert w.bill("4100").status is BillStatus.SKIPPED_TEXT_PREFILTER
    assert w.bill("4101").status is BillStatus.SKIPPED_TEXT_PREFILTER


def test_sejm_api_outage_leaves_bills_pending() -> None:
    w = World()
    w.add_bill("4100", "Rządowy projekt ustawy o podatku")
    w.gateway.outages.add("download")

    report = w.run()

    assert any(e.startswith("text prefilter:") for e in report.errors)
    assert w.bill("4100").status is BillStatus.TEXT_PREFILTER_PENDING


def test_text_prefilter_can_be_disabled() -> None:
    w = World(text_prefilter=False, extractor=FakeTextExtractor(FOREIGNER_TEXT))
    w.add_bill("4100", "Rządowy projekt ustawy o podatku")

    w.run()

    assert w.bill("4100").status is BillStatus.SKIPPED_PREFILTER
    assert not any(c.startswith("download:") for c in w.gateway.calls)

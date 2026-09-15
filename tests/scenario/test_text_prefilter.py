"""The second prefilter stage: keyword search inside the print PDF when the title said nothing."""

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import BillStatus
from tests.fakes import FakeTextExtractor
from tests.harness import RPW, World, submission, submission_url

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


def test_weak_title_hit_alone_goes_to_the_text_stage_not_to_the_model() -> None:
    fuel_quality = "Art. 1. Straż Graniczna kontroluje jakość paliw na przejściach. " * 20
    w = World(extractor=FakeTextExtractor(fuel_quality))
    w.add_bill("4100", "Rządowy projekt ustawy o zmianie ustawy o Straży Granicznej")

    report = w.run()

    assert (report.prefilter_hits, report.text_prefilter_checked) == (0, 1)
    assert report.analyzed == 0 and w.llm.contexts == []
    bill = w.bill("4100")
    assert bill.status is BillStatus.SKIPPED_TEXT_PREFILTER
    assert bill.prefilter_hits == ["text:straz_graniczna"]  # the title's weak hit, kept for tuning


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
    # The reason names the threshold, not "weak patterns": cudzoziemcy is the strongest
    # pattern there is, it simply occurred once.
    assert bill.last_error == (
        "text prefilter: under the threshold of 2 distinct patterns (cudzoziemcy×1)"
    )


def test_a_broken_pdf_is_a_verdict_and_a_file_not_there_yet_is_not() -> None:
    """The Sejm lists a process before the print's file is attached to it, so "no file" is a
    fact about the day: druk 3094 was judged five minutes before its PDF appeared (15 Sept 2026)
    and the skip closed the row for good. A file that downloads and cannot be read is the other
    thing, and stays a verdict."""
    w = World(extractor=FakeTextExtractor(error=ValueError("not a PDF")))
    w.add_bill("4100", "Rządowy projekt ustawy o podatku")
    w.add_bill("4101", "Rządowy projekt ustawy o lasach", with_pdf=False)

    report = w.run()

    assert report.text_prefilter_checked == 2 and not report.errors
    assert (report.text_prefilter_unreadable, report.text_prefilter_unanswered) == (1, 1)
    broken, missing = w.bill("4100"), w.bill("4101")
    # The reason is on record: a skip for lack of a text is not a keyword miss.
    assert broken.status is BillStatus.SKIPPED_TEXT_PREFILTER
    assert broken.last_error == "text prefilter failed: ValueError: not a PDF"
    assert missing.status is BillStatus.TEXT_PREFILTER_PENDING
    assert missing.last_error == "text prefilter: the print has no file yet"


def test_a_scanned_print_goes_to_the_model_instead_of_being_skipped() -> None:
    """Keywords cannot search a photograph of paper, and that is not a reason to drop the bill:
    the deputies' prints that arrive as scans are the ones whose titles say the least."""
    w = World(extractor=FakeTextExtractor("", page_count=12))
    w.add_bill("4100", "Poselski projekt ustawy o zmianie niektórych ustaw")

    report = w.run()

    assert (report.text_prefilter_scans, report.text_prefilter_unreadable) == (1, 0)
    assert report.text_prefilter_hits == 0  # not a keyword hit: nothing was searched
    assert report.analyzed == 1
    ctx = w.llm.contexts[0]
    assert ctx.text_source == "scan" and ctx.scan is not None and ctx.scan.pages == 12


def test_a_file_with_neither_text_nor_pages_is_still_a_skip() -> None:
    w = World(extractor=FakeTextExtractor("", page_count=0))
    w.add_bill("4100", "Poselski projekt ustawy o zmianie niektórych ustaw")

    report = w.run()

    assert (report.text_prefilter_scans, report.text_prefilter_unreadable) == (0, 1)
    bill = w.bill("4100")
    assert bill.status is BillStatus.SKIPPED_TEXT_PREFILTER
    assert bill.last_error == "text prefilter: no text layer and no pages to read"


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


COVER_LETTER = (
    "Druk nr 604       Warszawa, 23 lipca 2024 r.\n"
    "SEJM\nRZECZYPOSPOLITEJ POLSKIEJ\nX kadencja\n"
    "Na podstawie art. 118 ust. 1 Konstytucji Rzeczypospolitej Polskiej i na podstawie\n"
    "art. 32 ust. 2 regulaminu Sejmu niżej podpisani posłowie wnoszą projekt ustawy:\n"
    " - o zmianie ustawy o Krajowej Administracji Skarbowej.\n"
    "Do reprezentowania wnioskodawców upoważniamy pana posła Ryszarda Petru.\n"
    " (-)  Elżbieta Burkiewicz;  (-)  Żaneta Cwalina-Śliwowska;  (-)  Sławomir Ćwik\n"
)


def test_a_print_that_is_only_its_covering_letter_goes_to_the_model_unsearched() -> None:
    """The prefilter has to ask the same question the analysis asks.

    `TextLoader` calls a file textless only under `MIN_TEXT_CHARS`, so a print whose text layer
    is the letter that hands it to the Marshal — 700-1,200 characters — arrived looking like a
    document, was searched for keywords that a transmittal note never contains, and was skipped
    for good. Measured over term 10: **91 of the 938 prints** are that case, every one of them
    with pages the model could have read, and the invariant is that a file keywords cannot
    search is not a file to drop.
    """
    w = World(extractor=FakeTextExtractor(COVER_LETTER, page_count=37))
    w.add_bill("4100", "Poselski projekt ustawy o zmianie niektórych ustaw")

    report = w.run()

    assert (report.text_prefilter_checked, report.text_prefilter_scans) == (1, 1)
    assert report.text_prefilter_hits == 0  # nothing was searched: there was nothing to search
    assert w.bill("4100").status is not BillStatus.SKIPPED_TEXT_PREFILTER


def test_a_file_the_waf_refuses_leaves_the_bill_pending() -> None:
    """A refusal from orka.sejm.gov.pl is a decision about our address on the day, not about the
    bill: RPW/30695/2026 was written off on 2026-09-14 by a 403 that had cleared by the
    afternoon, and `skipped_text_prefilter` is where a bill stays until an operator digs it out.
    """
    w = World()
    w.gateway.submissions.append(submission(title="Rządowy projekt ustawy o ubezpieczeniach"))
    w.orka.refuses.add(submission_url())

    report = w.run()

    assert (report.text_prefilter_checked, report.text_prefilter_unanswered) == (1, 1)
    assert report.text_prefilter_unreadable == 0
    bill = w.bill(RPW)
    assert bill.status is BillStatus.TEXT_PREFILTER_PENDING
    assert bill.last_error == (
        f"text prefilter: OrkaUnreachableError: GET {submission_url()}: HTTP 403"
    )


def test_a_file_that_is_not_there_is_still_a_skip() -> None:
    """A 404 is about the bill: the address is built by convention and can simply be wrong, so
    retrying it every run would queue the bill for ever."""
    w = World()
    w.gateway.submissions.append(submission(title="Rządowy projekt ustawy o ubezpieczeniach"))
    assert submission_url() not in w.gateway.files  # the Sejm never published it there

    report = w.run()

    assert (report.text_prefilter_unanswered, report.text_prefilter_unreadable) == (0, 1)
    assert w.bill(RPW).status is BillStatus.SKIPPED_TEXT_PREFILTER

"""Bills followed before they have a print number (RPW entries from /bills)."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import ApplicantType, BillStatus
from tests.fakes import FakeTextExtractor
from tests.harness import RPW, World, submission


def test_pre_print_bill_is_analysed_from_its_description_and_published() -> None:
    w = World()
    w.gateway.submissions.append(submission())

    report = w.run()

    assert (report.pre_print_discovered, report.analyzed, report.published) == (1, 1, 1)
    assert not any(c.startswith("get_process") for c in w.gateway.calls)
    ctx = w.llm.contexts[0]
    assert (ctx.number, ctx.text_source, ctx.applicant_type) == (
        RPW,
        "metadata_only",
        ApplicantType.DEPUTIES,
    )
    bill, print_info = w.publisher.new_bills[0]
    assert bill.is_pre_print and print_info is None
    assert bill.submission is not None
    assert bill.submission.consultation_end == dt.date(2026, 9, 30)


def test_pre_print_card_names_the_stage_and_links_the_sejm_pdf() -> None:
    w = World()
    w.gateway.submissions.append(submission())
    w.run()

    text = MessageFormatter("ru").new_bill(w.bill(RPW), None, today=w.clock.now().date()).text

    assert "RPW/29075/2026 (номер druku ещё не присвоен)" in text
    assert "Общественные консультации:</b> 02.09.2026 — 30.09.2026" in text
    assert "orka.sejm.gov.pl/Druki10ka.nsf/Projekty/10-RPW-29075-2026/" in text
    assert "#RPW_29075_2026" in text and "ожидает присвоения номера druku" in text


def test_pre_print_bill_is_not_polled_as_a_process() -> None:
    w = World()
    w.gateway.submissions.append(submission())
    w.run()
    w.clock.advance(days=1)

    report = w.run()

    assert (report.published, report.updates, report.tracked) == (0, 0, 0)


def test_assigned_print_number_continues_the_thread_under_the_new_number() -> None:
    w = World()
    w.gateway.submissions.append(submission())
    w.run()
    card_id = w.card_id(RPW)
    w.gateway.submissions[0] = submission(print_number="3100")
    w.add_bill("3100", "Poselski projekt ustawy o zmianie ustawy o udzielaniu cudzoziemcom ochrony")
    w.touch("3100", dt.datetime(2026, 9, 8, 9, 0))
    w.clock.advance(days=1)

    report = w.run()

    assert (report.linked, report.published, report.updates, report.reanalyzed) == (1, 0, 1, 1)
    assert len(w.publisher.new_bills) == 1  # no second card
    bill, change, reply_to = w.publisher.updates[0]
    assert (bill.number, bill.linked_number, reply_to) == ("3100", RPW, card_id)
    assert change.content_changed  # the real print text replaced the metadata analysis
    assert [st.stage_type for st in change.new_stages] == ["Start"]
    text = MessageFormatter("ru").status_update(bill, change).text
    assert "🔢 <b>Присвоен номер druku — druk nr 3100</b>" in text
    assert "Проекту присвоен номер druku: <b>3100</b>" in text
    assert "#kadencja10druk3100 #RPW_29075_2026" in text  # either tag finds the thread
    pre = w.bill(RPW)
    assert (pre.status, pre.linked_number) == (BillStatus.LINKED, "3100")
    edited, edited_message = w.publisher.edits[0]  # the card now carries the druk's tag too
    assert (edited.number, edited_message) == (RPW, card_id)
    assert (
        "#RPW_29075_2026 #kadencja10druk3100" in MessageFormatter("ru").new_bill(edited, None).text
    )
    analysis = w.bill("3100").analysis
    assert analysis is not None and analysis.revision == 2


def test_after_linking_only_the_print_is_tracked() -> None:
    w = World()
    w.gateway.submissions.append(submission())
    w.run()
    w.gateway.submissions[0] = submission(print_number="3100")
    w.add_bill("3100", "Poselski projekt ustawy o zmianie ustawy o udzielaniu cudzoziemcom ochrony")
    w.clock.advance(days=1)
    w.run()
    w.clock.advance(days=1)

    report = w.run()

    assert (report.tracked, report.updates, report.linked) == (1, 0, 0)


def test_withdrawn_pre_print_bill_is_announced_once() -> None:
    w = World()
    w.gateway.submissions.append(submission())
    w.run()
    w.gateway.submissions[0] = submission(status="WITHDRAWN", withdrawn_date=dt.date(2026, 9, 5))
    w.clock.advance(days=1)

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    assert report.updates == 1
    bill, change, _ = w.publisher.updates[0]
    assert change.withdrawn and change.closure_detected
    assert "Проект отозван" in MessageFormatter("ru").status_update(bill, change).text
    assert again.updates == 0


def test_numbered_print_takes_consultation_dates_and_applicant_from_its_bills_entry() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")  # the title gives no applicant
    w.gateway.submissions.append(
        submission(number="RPW/26666/2026", print_number="3039", applicant=ApplicantType.GOVERNMENT)
    )

    w.run()

    bill, _ = w.publisher.new_bills[0]
    assert bill.summary.applicant_type is ApplicantType.GOVERNMENT
    assert "Общественные консультации" in MessageFormatter("ru").new_bill(bill, None).text
    assert w.repo.get(10, "RPW/26666/2026") is None  # never seen as pre-print: no phantom row


def test_numbered_closed_and_non_bill_submissions_are_not_pre_print_bills() -> None:
    w = World()
    w.gateway.submissions.append(submission(number="RPW/1/2026", print_number="9"))
    w.gateway.submissions.append(submission(number="RPW/2/2026", status="WITHDRAWN"))
    w.gateway.submissions.append(
        submission(number="RPW/3/2026", submission_type="DRAFT_RESOLUTION")
    )

    report = w.run()

    assert (report.pre_print_discovered, report.discovered) == (0, 0)


NEUTRAL_TITLE = "Poselski projekt ustawy o zmianie ustawy o podatku"
FOREIGNER_TEXT = (
    "Art. 1. W ustawie o cudzoziemcach wprowadza się zmiany. "
    "Art. 2. Zezwolenie na pobyt czasowy wydaje wojewoda. " * 3
)


def test_print_of_a_title_miss_entry_goes_through_the_text_prefilter() -> None:
    # The RPW entry has no PDF, so a title miss ends there; the print has one and must be scanned
    # instead of inheriting the skip.
    w = World(extractor=FakeTextExtractor(FOREIGNER_TEXT))
    w.gateway.submissions.append(submission(title=NEUTRAL_TITLE, description="zmiany podatkowe"))
    w.run()
    assert w.bill(RPW).status is BillStatus.SKIPPED_PREFILTER
    w.gateway.submissions[0] = submission(
        title=NEUTRAL_TITLE, description="zmiany podatkowe", print_number="3100"
    )
    w.add_bill("3100", NEUTRAL_TITLE)
    w.touch("3100", dt.datetime(2026, 9, 8, 9, 0))
    w.clock.advance(days=1)

    linked = w.run()
    after_link = w.bill("3100").status
    w.clock.advance(days=1)
    scanned = w.run()

    assert (linked.linked, after_link) == (1, BillStatus.TEXT_PREFILTER_PENDING)
    assert (scanned.text_prefilter_checked, scanned.analyzed, scanned.published) == (1, 1, 1)
    bill, print_info = w.publisher.new_bills[0]
    assert (bill.number, bill.status) == ("3100", BillStatus.ANALYZED)
    assert print_info is not None and "text:cudzoziemcy" in bill.prefilter_hits
    assert w.bill(RPW).status is BillStatus.LINKED


def test_print_of_a_title_miss_entry_stays_skipped_without_the_text_prefilter() -> None:
    w = World(text_prefilter=False, extractor=FakeTextExtractor(FOREIGNER_TEXT))
    w.gateway.submissions.append(submission(title=NEUTRAL_TITLE, description="zmiany podatkowe"))
    w.run()
    w.gateway.submissions[0] = submission(
        title=NEUTRAL_TITLE, description="zmiany podatkowe", print_number="3100"
    )
    w.add_bill("3100", NEUTRAL_TITLE)
    w.touch("3100", dt.datetime(2026, 9, 8, 9, 0))
    w.clock.advance(days=1)

    report = w.run()

    assert (report.linked, w.bill("3100").status) == (1, BillStatus.SKIPPED_PREFILTER)
    assert not any(c.startswith("download:") for c in w.gateway.calls)


def test_an_entry_the_sejm_stopped_listing_does_not_wait_forever() -> None:
    """Its thread would otherwise end at "ждём номер druku" and be re-queried for years."""
    w = World()
    w.gateway.submissions.append(submission())
    w.run()
    w.gateway.submissions[0] = submission(number="RPW/1/2026", title="Inny projekt")
    w.clock.advance(days=400)

    report = w.run()

    assert report.updates == 1
    bill, change, _ = w.publisher.updates[0]
    assert bill.number == RPW
    assert change.withdrawn and change.closure_detected


def test_an_entry_missing_for_a_few_days_is_left_alone() -> None:
    w = World()
    w.gateway.submissions.append(submission())
    w.run()
    w.gateway.submissions[0] = submission(number="RPW/1/2026", title="Inny projekt")
    w.clock.advance(days=30)

    report = w.run()

    assert report.updates == 0

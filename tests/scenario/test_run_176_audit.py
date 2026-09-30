"""Regressions for the review of production runs 175-176 (B66, B70)."""

from lexinform.models import BillStatus
from tests.fakes import FakeTextExtractor, make_analysis
from tests.harness import RPW, World, submission, submission_url

BUDGET = "Rządowy projekt ustawy budżetowej na rok 2027"


def _entry_batched_then_its_print_skipped(w: World) -> None:
    """The entry's text is filed to a batch; its print arrives with a file the keywords miss."""
    w.gateway.submissions.append(submission(title=BUDGET))
    w.gateway.files[submission_url()] = b"%PDF-rpw"
    w.run()
    assert w.bill(RPW).status is BillStatus.BATCH_PENDING
    w.clock.advance(days=1)
    w.gateway.submissions[0] = submission(title=BUDGET, print_number="3100")
    w.add_bill("3100", BUDGET)
    w.run()


def test_a_print_its_own_text_already_skipped_is_not_scanned_again_when_its_entry_links() -> None:
    w = World()
    w.gateway.submissions.append(submission(title=BUDGET))
    w.run()
    assert w.bill(RPW).status is BillStatus.SKIPPED_TEXT_PREFILTER
    w.clock.advance(days=1)
    w.add_bill("3100", BUDGET)
    w.run()
    assert w.bill("3100").status is BillStatus.SKIPPED_TEXT_PREFILTER
    w.gateway.submissions[0] = submission(title=BUDGET, print_number="3100")
    w.clock.advance(days=1)
    w.run()
    linked = w.bill("3100").status
    w.clock.advance(days=1)

    report = w.run()

    assert w.bill(RPW).status is BillStatus.LINKED
    assert linked is BillStatus.SKIPPED_TEXT_PREFILTER
    assert report.text_prefilter_checked == 0


def test_an_entrys_batched_analysis_reaches_the_print_it_became() -> None:
    w = World(
        batch=True,
        extractor=FakeTextExtractor(by_content={b"%PDF-rpw": "cudzoziemcy uchodźcy azyl " * 40}),
        batch_script={RPW: make_analysis(score=4)},
    )
    _entry_batched_then_its_print_skipped(w)
    assert w.bill(RPW).status is BillStatus.LINKED
    w.batch.resolve()
    w.clock.advance(hours=1)

    w.run()
    w.clock.advance(hours=1)
    w.run()

    druk = w.bill("3100")
    assert druk.analysis is not None and druk.analysis.analysis.score == 4
    first = [r.number for r in w.batch.submitted if r.question.kind == "analysis"]
    assert first == [RPW]
    assert [bill.number for bill, _ in w.publisher.new_bills] == ["3100"]

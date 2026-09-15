"""What the text-prefilter backfill tells the log channel."""

import datetime as dt

from lexinform.adapters.telegram_format import MESSAGE_LIMIT, MessageFormatter, length
from lexinform.models import BackfillOutcome, BackfillReport

STARTED = dt.datetime(2026, 9, 15, 13, 19, 54, tzinfo=dt.UTC)


def _report(*outcomes: BackfillOutcome, **fields: object) -> BackfillReport:
    return BackfillReport(
        started_at=STARTED,
        finished_at=STARTED + dt.timedelta(minutes=30),
        limit=200,
        include_text_skipped=True,
        outcomes=list(outcomes),
        **fields,
    )


def test_the_queued_bills_are_named_because_they_are_what_the_run_will_pay_for() -> None:
    """Run 69 of 15 Sept 2026 spent 29 of its 32 minutes in the backfill and queued 14 bills; the
    report that reached the channel said "text prefilter: checked 1" and listed 14 analysis
    candidates with no account of where they came from."""
    report = _report(
        BackfillOutcome(
            number="2600",
            title="Poselski projekt ustawy o ochotniczych strażach pożarnych",
            accepted=True,
            hits=("text:straz_graniczna",),
        ),
        BackfillOutcome(
            number="RCL/12413251",
            title="Projekt ustawy – Prawo energetyczne",
            accepted=False,
            reason="text prefilter: no keyword hits",
        ),
    )

    text = MessageFormatter("ru").backfill_report(report).text

    assert "scanned: 2 · queued for analysis: 1" in text
    assert "druk 2600" in text and "text:straz_graniczna" in text
    assert "RCL/12413251" not in text  # a skip is a count, not a line
    assert "1800s" in text and "title and text skips" in text


def test_a_scan_says_it_has_no_keywords_rather_than_showing_an_empty_list() -> None:
    """A file with pages and no text layer goes to the model unsearched, so it is queued with no
    hits at all — which read as a bill that matched nothing."""
    report = _report(
        BackfillOutcome(number="2821", title="Rządowy projekt ustawy o OZE", accepted=True)
    )

    text = MessageFormatter("ru").backfill_report(report).text

    assert "druk 2821 · no keywords (a scan)" in text


def test_a_backfill_that_found_nothing_still_says_so() -> None:
    text = MessageFormatter("ru").backfill_report(_report()).text

    assert "nothing was skipped" in text
    assert "queued" not in text


def test_the_message_fits_telegram_however_many_bills_were_queued() -> None:
    many = [
        BackfillOutcome(
            number=str(n), title="Rządowy projekt ustawy o zmianie ustawy " * 4, accepted=True
        )
        for n in range(2000, 2200)
    ]

    text = MessageFormatter("ru").backfill_report(_report(*many)).text

    assert length(text) <= MESSAGE_LIMIT
    assert "scanned: 200 · queued for analysis: 200" in text


def test_errors_are_shown_and_mark_the_backfill_failed() -> None:
    report = _report(errors=["RCL: request rejected"])

    text = MessageFormatter("ru").backfill_report(report).text

    assert text.startswith("<b>❌ lexinform backfill</b>")
    assert "RCL: request rejected" in text

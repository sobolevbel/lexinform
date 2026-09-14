"""An outage of an external system ends its phase with a clear error and never crashes the run,
and it never burns the per-bill budget of attempts either: what failed because the Sejm, the model
or Telegram was down must be tried again on the next run, and exactly once."""

from datetime import datetime

import pytest

from lexinform.errors import LlmUnavailableError
from lexinform.models import BillStatus, PublicationStatus
from tests.harness import World


def test_sejm_api_down_during_discovery_is_reported_not_raised() -> None:
    w = World()
    w.gateway.outages.add("iter_processes")

    report = w.run()

    assert not report.ok
    assert report.errors == ["discovery: Sejm API unavailable: iter_processes: connection refused"]
    assert w.notifier.calls[-1][0] is report


def test_sejm_api_down_during_analysis_keeps_the_attempts() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.gateway.outages.add("get_process")

    report = w.run()

    assert any("analysis: Sejm API unavailable" in e for e in report.errors)
    bill = w.bill("3039")
    assert (bill.analysis_attempts, bill.status) == (0, BillStatus.ANALYSIS_PENDING)
    assert w.llm.contexts == []


def test_llm_down_stops_the_phase_without_consuming_attempts() -> None:
    w = World(
        llm_script={
            "3039": LlmUnavailableError("RateLimitError: 429"),
            "3040": LlmUnavailableError("x"),
        }
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")

    report = w.run()

    assert report.errors == ["analysis: LLM API unavailable: RateLimitError: 429"]
    assert [w.bill(n).analysis_attempts for n in ("3039", "3040")] == [0, 0]
    assert len(w.llm.contexts) == 1  # stopped after the first outage


def test_telegram_down_marks_the_post_failed_and_stops_publishing() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")
    w.publisher.outage_on = {"3039", "3040"}

    report = w.run()

    assert report.errors == [
        "publishing: Telegram API unavailable: sendMessage: ConnectError after 3 attempts"
    ]
    first = w.publication("3039")
    assert first is not None and first.status is PublicationStatus.FAILED
    assert w.publication("3040") is None  # not even attempted


def test_sejm_api_down_while_completing_the_card_leaves_no_pending_row() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run(max_publish=0)  # discovered and analysed, the card still to be sent
    w.gateway.outages.add("find_submission")  # the consultation dates are read just before the post

    report = w.run()

    assert any(e.startswith("publishing: Sejm API unavailable") for e in report.errors)
    assert w.publication("3039") is None  # no pending row that would turn into `unknown`
    w.gateway.outages.clear()
    assert w.run().published == 1


def test_telegram_outages_do_not_use_up_the_retries_of_a_post() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.publisher.outage_on = {"3039"}
    for _ in range(3):  # a day of the bot being kicked out: three runs, three outages
        w.run()
    w.publisher.outage_on = set()

    report = w.run()

    assert report.published == 1
    card = w.publication("3039")
    assert card is not None and (card.status, card.attempts) == (PublicationStatus.SENT, 0)


def test_publishing_resumes_without_duplicates_when_telegram_is_back() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")
    w.publisher.outage_on = {"3039", "3040"}
    w.run()
    w.publisher.outage_on = set()

    report = w.run()

    assert report.published == 2
    assert len(w.publisher.new_bills) == 2


def test_unexpected_bug_in_a_phase_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    w = World()

    def bug(term: int, since: datetime, *, pre_print: bool = True) -> None:
        raise KeyError("oops")

    monkeypatch.setattr(w.discovery, "discover", bug)

    report = w.run(expect_bugs=True)

    assert report.errors == ["discovery failed: KeyError: 'oops'"]

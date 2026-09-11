"""The Sejm moves on to a new term (kadencja): the term comes from the API, the unfinished bills
of the old one lapse (zasada dyskontynuacji), passed bills and RCL projects stay followed."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import (
    ApplicantType,
    PublicationKind,
    PublicationStatus,
    SejmTerm,
    next_phase,
)
from tests.harness import (
    CHANNEL,
    COMMITTEE_STAGES,
    RCL,
    RPW,
    START,
    TERM,
    World,
    detail,
    submission,
    summary,
)

NEW_TERM = 11


def _sejm_moves_on(w: World) -> None:
    """The API now lists the next term as the running one; a day has passed."""
    w.gateway.terms = [
        SejmTerm(num=TERM, start=dt.date(2023, 11, 13), end=dt.date(2027, 11, 12)),
        SejmTerm(num=NEW_TERM, start=dt.date(2027, 11, 13), current=True),
    ]
    w.clock.advance(days=1)


def test_the_term_comes_from_the_api_when_not_pinned() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    report = w.run(term=None)

    assert report.term == TERM and report.published == 1
    assert f"iter_processes:{TERM}" in w.gateway.calls


def test_without_the_api_the_newest_term_in_the_database_is_used() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.gateway.outages.add("list_terms")
    w.clock.advance(days=1)

    report = w.run(term=None)

    assert report.term == TERM and not report.errors


def test_an_unknown_term_ends_the_run_with_a_clear_error() -> None:
    w = World()
    w.gateway.outages.add("list_terms")

    report = w.run(term=None)

    assert report.term is None
    assert report.errors == ["term: Sejm API unavailable: list_terms: connection refused"]
    assert not any(call.startswith("iter_processes") for call in w.gateway.calls)


def test_unfinished_bills_lapse_with_the_term_and_passed_ones_stay_followed() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES)
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")
    w.gateway.submissions.append(submission())
    w.run()
    w.touch("3040", dt.datetime(2026, 9, 8, 9, 0), closure_date=dt.date(2026, 9, 8), passed=True)
    w.clock.advance(days=1)
    w.run()  # the passage of 3040 is announced the usual way
    _sejm_moves_on(w)

    report = w.run(term=None)
    w.gateway.calls.clear()
    again = w.run(term=None, full_track=True)

    assert (report.term, report.discontinued, report.updates) == (NEW_TERM, 2, 2)
    assert report.discovered == 0  # the prints of the old term are not listed under the new one
    lapsed = {bill.number: change for bill, change, _ in w.publisher.updates[-2:]}
    assert set(lapsed) == {"3039", RPW}
    assert all(c.discontinued and c.closure_detected for c in lapsed.values())
    text = MessageFormatter("ru").status_update(*w.publisher.updates[-1][:2]).text
    assert "Каденция Сейма закончилась" in text and "#прекращён" in text
    assert w.bill("3039").discontinued_at is not None
    assert w.bill(RPW).discontinued_at is not None
    assert w.bill("3040").discontinued_at is None
    assert next_phase(w.bill("3039"), today=w.clock.now().date()) is None
    assert (again.discontinued, again.updates) == (0, 0)
    assert "get_process:3040" in w.gateway.calls  # passed, waiting for the act
    assert "get_process:3039" not in w.gateway.calls  # lapsed: not polled any more


def test_rollover_without_publishing_records_the_lapse_as_skipped() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES)
    w.run()
    _sejm_moves_on(w)

    silent = w.run(term=None, publish=False)
    w.clock.advance(days=1)
    again = w.run(term=None)

    assert silent.discontinued == 1 and w.publisher.updates == []
    assert w.bill("3039").discontinued_at is not None
    row = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert row is not None and row.status is PublicationStatus.SKIPPED  # decided, not lost
    assert (again.discontinued, again.updates) == (0, 0) and w.publisher.updates == []


def test_a_citizens_bill_is_taken_over_by_the_new_sejm() -> None:
    w = World()
    w.add_bill("3039", "Obywatelski projekt ustawy o cudzoziemcach")
    w.run()
    _sejm_moves_on(w)

    w.run(term=None)

    bill, change, _ = w.publisher.updates[-1]
    assert bill.summary.applicant_type is ApplicantType.CITIZENS and change.discontinued
    text = MessageFormatter("ru").status_update(bill, change).text
    assert "переходит к Сейму новой каденции" in text


def test_rcl_project_follows_the_sejm_and_its_druk_in_the_new_term_takes_over() -> None:
    w = World()
    project = w.add_rcl_project()
    w.run()
    card_id = w.card_id(RCL)
    _sejm_moves_on(w)
    w.rcl.rm_numbers["RM-0610-139-26"] = project.id

    moved = w.run(term=None)
    druk = summary("5", project.title, change="2027-11-14T09:00:00").model_copy(
        update={"term": NEW_TERM, "rcl_num": "RM-0610-139-26"}
    )
    w.gateway.processes.append(druk)
    w.gateway.details["5"] = detail(druk, START)
    w.clock.advance(days=1)
    linked = w.run(term=None)

    assert moved.rcl_rehomed == 1 and w.repo.get(TERM, RCL) is None
    carried = w.repo.get(NEW_TERM, RCL)
    assert carried is not None and carried.rcl is not None and carried.analysis is not None
    card = w.repo.get_publication(NEW_TERM, RCL, "new_bill", CHANNEL)
    assert card is not None and card.message_id == card_id
    assert (linked.published, linked.linked) == (0, 1)  # no second card for the druk
    bill, _, reply_to = w.publisher.updates[-1]
    assert (bill.term, bill.number, bill.linked_number, reply_to) == (NEW_TERM, "5", RCL, card_id)


def test_telegram_outage_during_the_rollover_leaves_the_bills_for_the_next_run() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    _sejm_moves_on(w)
    w.publisher.outage_on = {"3039"}

    failed = w.run(term=None)
    marked_after_failure = w.bill("3039").discontinued_at
    w.publisher.outage_on = set()
    w.clock.advance(days=1)
    retried = w.run(term=None)

    assert failed.discontinued == 0 and marked_after_failure is None
    assert any("end of term: Telegram API unavailable" in e for e in failed.errors)
    assert retried.updates == 1 and not retried.errors  # the recorded update is sent
    assert w.bill("3039").discontinued_at is not None
    assert sum(1 for bill, _, _ in w.publisher.updates if bill.number == "3039") == 1

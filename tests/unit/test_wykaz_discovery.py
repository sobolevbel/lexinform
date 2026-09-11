"""Discovery of planned bills: what the register makes a row, and what it deliberately does not."""

import datetime as dt

from lexinform.models import BillStatus
from tests.harness import RCL, WYKAZ, World, rcl_project, wykaz_entry

OLD = dt.datetime(2026, 5, 12, 15, 21, tzinfo=dt.UTC)  # published before the run's watermark


def test_an_announced_bill_becomes_a_row_analysed_from_the_registers_own_words() -> None:
    w = World()
    w.add_wykaz_entry()

    report = w.run()

    assert (report.wykaz_discovered, report.analyzed, report.published) == (1, 1, 1)
    ctx = w.llm.contexts[0]
    assert (ctx.number, ctx.text_source, ctx.source_kind) == (WYKAZ, "metadata_only", "metadata")
    assert ctx.description is not None and "milczącego zakończenia" in ctx.description
    bill = w.bill(WYKAZ)
    assert bill.is_wykaz and not bill.has_process
    assert bill.wykaz is not None and bill.wykaz.organ == "MSWiA"
    assert bill.stages == ()  # one announcement is not a timeline
    assert "cudzoziemcy" in bill.prefilter_hits


def test_a_plan_that_says_nothing_about_foreigners_is_not_even_stored() -> None:
    w = World()
    w.add_wykaz_entry(
        number="UD500",
        title="Projekt ustawy o zmianie ustawy o rachunkowości",
        goals="Uproszczenie sprawozdawczości finansowej.",
        essence="Podniesienie progów dla jednostek mikro.",
    )

    report = w.run()

    assert (report.wykaz_discovered, report.wykaz_backlog) == (0, 0)
    assert w.repo.find_wykaz("WPL/UD500") is None


def test_an_older_entry_is_reported_as_backlog_and_left_alone() -> None:
    w = World()
    w.add_wykaz_entry(published_at=OLD)

    report = w.run()

    assert (report.wykaz_discovered, report.wykaz_backlog) == (0, 1)
    assert w.repo.find_wykaz(WYKAZ) is None
    assert w.publisher.new_bills == []


def test_a_backlog_entry_is_taken_when_the_run_is_told_to_look_that_far_back() -> None:
    w = World()
    w.add_wykaz_entry(published_at=OLD)

    report = w.run(since=dt.datetime(2026, 5, 1, tzinfo=dt.UTC))

    assert report.wykaz_discovered == 1
    assert w.repo.find_wykaz(WYKAZ) is not None


def test_a_plan_the_government_has_already_finished_with_gets_no_card() -> None:
    w = World()
    w.add_wykaz_entry(status="Zrealizowany")
    w.add_wykaz_entry(entry=wykaz_entry(number="UD409", status="Wycofany"))

    report = w.run()

    # A card invites action, and there is none left to take on either of these.
    assert (report.wykaz_discovered, report.published) == (0, 0)
    assert w.repo.find_wykaz(WYKAZ) is None


def test_a_plan_whose_project_is_already_followed_on_rcl_does_not_start_a_second_thread() -> None:
    w = World()
    w.add_rcl_project(rcl_project(wykaz_number="UD408"))
    w.run()

    w.add_wykaz_entry()
    report = w.run()

    assert report.wykaz_discovered == 0
    assert w.repo.find_wykaz(WYKAZ) is None
    assert w.publication(RCL) is not None


def test_rozporzadzenia_and_programmes_are_read_but_not_followed() -> None:
    w = World()
    w.add_wykaz_entry(
        number="RD238",
        kind="Projekty rozporządzeń",
        title="Projekt rozporządzenia w sprawie pomocy obywatelom Ukrainy",
    )

    report = w.run()

    assert (report.wykaz_discovered, report.wykaz_backlog) == (0, 0)
    assert w.repo.find_wykaz("WPL/RD238") is None


def test_a_known_entry_is_not_discovered_twice() -> None:
    w = World()
    w.add_wykaz_entry()
    w.run()

    report = w.run()

    assert report.wykaz_discovered == 0
    assert w.bill(WYKAZ).status is BillStatus.ANALYZED
    assert len(w.publisher.new_bills) == 1


def test_a_gov_pl_outage_costs_only_its_own_phase() -> None:
    w = World()
    w.add_wykaz_entry()
    w.add_bill("3039", "Projekt ustawy o zmianie ustawy o cudzoziemcach")
    w.wykaz.outage = True

    report = w.run()

    assert report.errors == ["wykaz discovery: wykaz prac RM unavailable: gov.pl does not answer"]
    assert (report.discovered, report.analyzed, report.published) == (1, 1, 1)
    assert w.publication("3039") is not None


def test_the_register_is_read_once_per_run() -> None:
    w = World()
    w.add_wykaz_entry()

    w.run()

    assert w.wykaz.calls == 1

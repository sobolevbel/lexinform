"""Discovery of planned bills: what the register makes a row, and what it deliberately does not."""

import datetime as dt

from lexinform.models import BillStatus
from tests.harness import RCL, RCL_ID, WYKAZ, World, rcl_project, wykaz_entry

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

    assert report.wykaz_discovered == 0
    assert w.repo.find_wykaz("WPL/UD500") is None


def test_an_entry_older_than_the_watermark_is_judged_like_every_other() -> None:
    """The register arrives whole every run and `Data publikacji` never moves, so a date gate
    hid an entry rewritten into relevance for ever — and filled the report with a backlog that
    yielded nothing."""
    w = World()
    w.add_wykaz_entry(published_at=OLD)

    report = w.run()

    assert report.wykaz_discovered == 1
    assert w.repo.find_wykaz(WYKAZ) is not None


def test_a_plan_the_government_finished_with_long_ago_is_not_this_run_s_news() -> None:
    """It is judged (and gets no card), but `over_on_arrival` counts the bills a run *met*."""
    w = World()
    w.add_wykaz_entry(published_at=OLD, status="Zrealizowany")

    report = w.run()

    assert (report.wykaz_discovered, report.over_on_arrival) == (0, 0)
    assert w.repo.find_wykaz(WYKAZ) is None


def test_a_plan_the_government_has_already_finished_with_gets_no_card() -> None:
    w = World()
    w.add_wykaz_entry(status="Zrealizowany")
    w.add_wykaz_entry(entry=wykaz_entry(number="UD409", status="Wycofany"))

    report = w.run()

    # A card invites action, and there is none left to take on either of these.
    assert (report.wykaz_discovered, report.published) == (0, 0)
    assert report.over_on_arrival == 2
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


def test_the_numbers_rcl_has_published_are_written_down_from_the_listing_alone() -> None:
    """The listing is the one place that join is published, and reading it costs nothing beyond
    the pages: no timeline, no catalog, no row in the database."""
    w = World()
    w.add_rcl_project(rcl_project(wykaz_number="UD408", created=dt.date(2026, 9, 2)))
    service = w.container.rcl_discovery_service()
    assert service is not None

    indexed = service.index_numbers(dt.date(2026, 1, 1))

    assert indexed == 1
    assert w.repo.find_rcl_project_of_plan("UD408", dt.date(2026, 9, 1)) == RCL_ID
    assert w.repo.find_rcl(RCL) is None  # the walk stores no bill
    assert w.rcl.calls == ["list_projects"]


def test_a_plan_whose_project_rcl_published_already_hands_over_the_project() -> None:
    """A card for a plan says there is no text yet, and nothing ever takes that back: the linker
    only sees a project the listing shows as changed, and a project can sit untouched for a year
    (UD344's has since January 2026). So the plan gets no card — and the project it names is
    taken, because the walk of the listing will never reach it either."""
    w = World()
    quiet = rcl_project(wykaz_number="UD408", created=dt.date(2026, 9, 2), modified=OLD.date())
    w.add_rcl_project(quiet)
    w.rcl.listing = []  # untouched since the watermark: the listing does not show it
    w.repo.remember_rcl_wykaz_number("UD408", RCL_ID, dt.date(2026, 9, 2))
    w.add_wykaz_entry()  # announced 2026-09-01, the day before the project came out

    report = w.run()

    assert report.wykaz_discovered == 0
    assert w.repo.find_wykaz(WYKAZ) is None
    followed = w.repo.find_rcl(RCL)
    assert followed is not None and followed.rcl is not None
    assert (report.rcl_discovered, report.published) == (1, 1)


def test_a_project_older_than_the_plan_held_the_number_before_it() -> None:
    """The register reuses its numbers: UD368 named a Centralny Port Komunikacyjny project in
    2018 and the Karta Polaka plan in 2026. Only a project published since the plan was announced
    can be the project of that plan."""
    w = World()
    w.repo.remember_rcl_wykaz_number("UD408", 12310959, dt.date(2018, 4, 27))
    w.add_wykaz_entry()

    report = w.run()

    assert (report.wykaz_discovered, report.published) == (1, 1)


def test_rozporzadzenia_and_programmes_are_read_but_not_followed() -> None:
    w = World()
    w.add_wykaz_entry(
        number="RD238",
        kind="Projekty rozporządzeń",
        title="Projekt rozporządzenia w sprawie pomocy obywatelom Ukrainy",
    )

    report = w.run()

    assert report.wykaz_discovered == 0
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


def test_the_register_is_asked_for_by_both_phases_and_downloaded_once() -> None:
    # Discovery and tracking share the client's copy of the file (see `WykazClient.entries`).
    w = World()
    w.add_wykaz_entry()

    w.run()

    assert w.wykaz.calls == 2

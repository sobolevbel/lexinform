"""Government projects found on RCL: discovery, prefilter, analysis from their documents."""

import datetime as dt

import pytest

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import BillStatus
from tests.fakes import FakeTextExtractor
from tests.harness import (
    CONSULTATION_LETTER,
    RCL,
    World,
    rcl_document,
    rcl_folder,
    rcl_project,
    rcl_stage,
)

FOREIGNER_TEXT = "Art. 1. Cudzoziemiec składa wniosek o zezwolenie na pobyt czasowy. " * 40


def test_new_project_is_analysed_from_its_documents_and_published() -> None:
    w = World()
    w.add_rcl_project()

    report = w.run()

    assert (report.rcl_discovered, report.rcl_prefilter_hits) == (1, 1)
    assert (report.analyzed, report.published, report.discovered) == (1, 1, 0)
    ctx = w.llm.contexts[0]
    assert (ctx.number, ctx.text_source, ctx.source_kind) == (RCL, "documents", "rcl")
    assert ctx.text.count("Art. 1.") >= 3  # bill + uzasadnienie (legacy .doc) + OSR
    bill, print_info = w.publisher.new_bills[0]
    assert bill.is_rcl and print_info is None
    assert bill.rcl is not None and bill.rcl.consultation is not None
    assert (bill.rcl.consultation.deadline, bill.rcl.consultation.email) == (
        dt.date(2026, 9, 8),  # 7 days from the letter published on 01-09-2026
        "dep.prawny@mswia.gov.pl",
    )
    assert bill.stages[-1].stage_name == "4. Opiniowanie"


def test_a_new_project_costs_one_page_and_catalogs_only_where_needed() -> None:
    w = World()
    candidate = w.add_rcl_project()
    miss = w.add_rcl_project(
        rcl_project(
            id=2,
            title="Projekt ustawy o zmianie niektórych ustaw",
            keywords=(),
            stages=candidate.stages,
        )
    )

    w.run(track=False)  # tracking would read the fresh candidate once more

    reads = [c for c in w.rcl.calls if c.startswith(("get_project", "get_stage"))]
    assert reads.count(f"get_project:{candidate.id}") == 1
    assert sum(1 for c in reads if c.startswith(f"get_stage:{candidate.id}/")) == 3  # reached
    assert reads.count(f"get_project:{miss.id}") == 1
    assert sum(1 for c in reads if c.startswith(f"get_stage:{miss.id}/")) == 1  # newest text


def test_card_of_an_rcl_project_offers_the_e_mail_and_the_comment_form() -> None:
    w = World()
    w.add_rcl_project()
    w.run()

    text = MessageFormatter("ru").new_bill(w.bill(RCL), None, today=dt.date(2026, 9, 7)).text

    assert "Правительственный проект (RCL) — UC164" in text
    assert "направить замечания на dep.prawny@mswia.gov.pl до 08.09.2026" in text
    assert f'<a href="{CONSULTATION_LETTER.url}">письмо о консультациях</a>' in text


def test_project_without_readable_text_is_analysed_from_its_description() -> None:
    w = World()
    project = rcl_project(
        consultation=None,
        stages=(
            rcl_stage(
                2,
                "Uzgodnienia",
                "active",
                rcl_folder(1, "Projekt", rcl_document(1, "projekt.rtf")),
            ),
        ),
    )
    w.add_rcl_project(project)

    report = w.run()

    assert (report.analyzed, report.published) == (1, 1)
    assert w.llm.contexts[0].text_source == "metadata_only"
    assert "не удалось прочитать —" in MessageFormatter("ru").new_bill(w.bill(RCL), None).text


def test_title_miss_is_caught_by_the_text_prefilter() -> None:
    w = World(extractor=FakeTextExtractor(FOREIGNER_TEXT))
    w.add_rcl_project(rcl_project(title="Projekt ustawy o zmianie niektórych ustaw", keywords=()))

    report = w.run()

    assert (report.rcl_prefilter_hits, report.text_prefilter_hits, report.published) == (0, 1, 1)
    assert any(h.startswith("text:") for h in w.bill(RCL).prefilter_hits)


def test_weak_title_hit_of_a_project_reads_its_text_not_its_catalogs() -> None:
    fuel_quality = "Art. 1. Straż Graniczna kontroluje jakość paliw na przejściach. " * 20
    w = World(extractor=FakeTextExtractor(fuel_quality))
    w.add_rcl_project(
        rcl_project(title="Projekt ustawy o zmianie ustawy o Straży Granicznej", keywords=())
    )

    report = w.run()

    assert (report.rcl_prefilter_hits, report.text_prefilter_checked, report.analyzed) == (0, 1, 0)
    assert w.bill(RCL).status is BillStatus.SKIPPED_TEXT_PREFILTER


def test_title_miss_without_documents_is_skipped_for_good() -> None:
    w = World()
    project = rcl_project(
        title="Projekt ustawy o zmianie niektórych ustaw",
        keywords=(),
        consultation=None,
        stages=(rcl_stage(2, "Uzgodnienia", "active"),),
    )
    w.add_rcl_project(project)

    report = w.run()

    assert (report.rcl_discovered, report.published) == (1, 0)
    assert w.bill(RCL).status is BillStatus.SKIPPED_PREFILTER
    assert not any(c.startswith("download") for c in w.rcl.calls)


def test_known_project_is_not_discovered_again_only_its_change_date_moves() -> None:
    w = World()
    project = w.add_rcl_project()
    w.run()
    reads_before = w.rcl.calls.count(f"get_project:{project.id}")
    w.rcl.put(project.model_copy(update={"modified": dt.date(2026, 9, 9)}))
    w.clock.advance(days=2)

    report = w.run(track=False)

    assert (report.rcl_discovered, report.published) == (0, 0)
    assert w.rcl.calls.count(f"get_project:{project.id}") == reads_before  # tracking reads it
    assert w.bill(RCL).summary.change_date == dt.datetime(2026, 9, 9, tzinfo=dt.UTC)


def test_rcl_outage_is_reported_and_leaves_the_sejm_discovery_intact() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_rcl_project()
    w.rcl.outages.add("list_projects")

    report = w.run()

    assert (report.discovered, report.published, report.rcl_discovered) == (1, 1, 0)
    assert any("rcl discovery: RCL unavailable" in e for e in report.errors)


def test_one_unreadable_project_page_does_not_stop_the_others() -> None:
    w = World()
    w.add_rcl_project()
    broken = rcl_project(id=1, title="Projekt ustawy o cudzoziemcach", stages=())
    w.rcl.listing.append(
        w.rcl.listing[0].model_copy(update={"id": 1, "title": broken.title})
    )  # listed, but its page is missing

    report = w.run()

    assert (report.rcl_discovered, report.published) == (1, 1)
    assert any("1 RCL project(s) could not be read" in e for e in report.errors)


@pytest.mark.parametrize("workers", [1, 4])
def test_parallel_reads_give_the_same_result(workers: int) -> None:
    w = World(workers=workers)
    w.add_rcl_project()
    w.add_rcl_project(rcl_project(id=2, title="Projekt ustawy o cudzoziemcach", stages=()))

    report = w.run()

    assert (report.rcl_discovered, report.rcl_prefilter_hits, report.published) == (2, 2, 2)

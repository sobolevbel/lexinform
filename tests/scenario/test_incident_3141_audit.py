"""Regressions for the findings of the incident 3141 review (B60-B63)."""

from lexinform.models import Attachment, BillStatus
from tests.fakes import FakeTextExtractor, make_analysis
from tests.harness import RCL, World, print_url, rcl_project
from tests.scenario.test_tracking_rcl import (
    RM,
    _druk_the_listing_does_not_link,
    _listing_names_the_project,
)


def _quiet_project_then_its_druk(w: World) -> None:
    """A project followed without a card; its druk arrives in a listing that does not name it."""
    project = w.add_rcl_project(rcl_project(rm_number=RM))
    w.run()
    w.batch.resolve()
    w.clock.advance(days=1)
    w.run()
    w.clock.advance(days=1)
    _druk_the_listing_does_not_link(w, project.title)
    w.run()


def test_a_druk_read_on_its_own_print_is_not_read_again_when_its_project_links() -> None:
    w = World(llm_script={RCL: make_analysis(score=2), "3100": make_analysis(score=2)})
    _quiet_project_then_its_druk(w)
    own = w.bill("3100").analysis
    _listing_names_the_project(w)
    w.clock.advance(days=1)

    w.run()
    w.clock.advance(days=1)
    w.run()

    assert own is not None and w.bill("3100").analysis == own
    assert len([ctx for ctx in w.llm.contexts if ctx.number == "3100"]) == 1


def test_a_druks_batched_first_analysis_survives_its_projects_link() -> None:
    w = World(batch=True, llm_script={RCL: make_analysis(score=2)})
    w.batch.script["3100"] = make_analysis(score=4)
    _quiet_project_then_its_druk(w)
    assert w.bill("3100").status is BillStatus.BATCH_PENDING
    _listing_names_the_project(w)
    w.clock.advance(days=1)
    w.run()
    w.batch.resolve()
    w.clock.advance(days=1)

    w.run()

    assert [request.number for request in w.batch.submitted].count("3100") == 1
    druk = w.bill("3100")
    assert druk.analysis is not None and druk.analysis.analysis.score == 4
    assert [bill.number for bill, _ in w.publisher.new_bills] == ["3100"]


def test_a_re_analysis_that_names_no_change_is_not_news_a_run_later() -> None:
    reformatted = "Art. 1. Ten sam tekst w innym układzie. " * 50
    w = World(
        batch=True,
        batch_kinds=frozenset({"analysis", "reanalysis", "supplement"}),
        extractor=FakeTextExtractor(by_content={b"%PDF-v2": reformatted}),
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.batch.resolve()
    w.run()
    w.clock.advance(days=2)
    revised = print_url("3039").replace("3039.pdf", "3039_v2.pdf")
    w.gateway.prints["3039"] = w.gateway.prints["3039"].model_copy(
        update={
            "change_date": w.clock.now().replace(tzinfo=None),
            "attachments": (Attachment(print_number="3039", name="3039_v2.pdf", url=revised),),
        }
    )
    w.gateway.files[revised] = b"%PDF-v2"
    w.batch.finds_no_changes = True
    w.run()
    w.batch.resolve()
    w.file_to_print("3039", "Do druku nr 3039 - ocena skutków regulacji")
    w.clock.advance(hours=1)
    waiting = w.run()
    w.batch.resolve()
    w.clock.advance(hours=1)

    posted = w.run()

    assert (waiting.reanalyzed, waiting.batch_waiting, posted.updates) == (1, 1, 1)
    _, change, _ = w.publisher.updates[-1]
    assert change.supplements and not change.content_changed


def test_the_first_text_of_a_plan_is_told_even_when_the_model_names_no_change() -> None:
    w = World()
    w.add_wykaz_entry()
    w.run()
    w.llm.finds_no_changes = True

    w.add_rcl_project(rcl_project(wykaz_number="UD408"))
    report = w.run()

    assert report.reanalyzed == 1
    _, change, _ = w.publisher.updates[0]
    assert change.content_changed


def test_a_project_whose_druk_the_database_knows_is_linked_without_the_api_walk() -> None:
    w = World(llm_script={RCL: make_analysis(score=2), "3100": make_analysis(score=3)})
    project = w.add_rcl_project(rcl_project(rm_number=RM))
    w.run()
    w.clock.advance(days=1)
    _druk_the_listing_does_not_link(w, project.title)  # the fake API walk cannot find it

    report = w.run()

    assert w.bill("3100").summary.rcl_num == RM
    assert report.linked == 1 and w.bill(RCL).status is BillStatus.LINKED
    assert [bill.number for bill, _ in w.publisher.new_bills] == ["3100"]

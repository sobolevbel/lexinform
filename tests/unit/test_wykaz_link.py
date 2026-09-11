"""A plan's project appears on RCL: one thread, one card, and the text is finally read."""

from lexinform.models import BillStatus, PublicationKind
from tests.fakes import make_analysis
from tests.harness import RCL, WYKAZ, World, rcl_project, wykaz_entry


def test_the_project_of_a_followed_plan_inherits_its_card_instead_of_getting_one() -> None:
    w = World()
    w.add_wykaz_entry()
    w.run()
    card = w.card_id(WYKAZ)

    w.add_rcl_project(rcl_project(wykaz_number="UD408"))
    report = w.run()

    assert report.rcl_discovered == 0  # not a new bill: the plan continues as this project
    assert len(w.publisher.new_bills) == 1
    project = w.publication(RCL)
    assert project is not None and project.message_id == card
    assert w.bill(WYKAZ).status is BillStatus.LINKED
    assert w.bill(RCL).linked_number == WYKAZ
    # The card is re-rendered once so that the thread's root carries both numbers.
    assert [number for _, number in ((b.number, m) for b, m in w.publisher.edits)] == [card]
    assert w.publisher.edits[0][0].number == WYKAZ


def test_the_text_is_analysed_when_the_project_brings_one() -> None:
    w = World()
    w.add_wykaz_entry()
    w.run()

    w.add_rcl_project(rcl_project(wykaz_number="UD408"))
    report = w.run()

    assert [ctx.text_source for ctx in w.llm.contexts] == ["metadata_only", "documents"]
    assert report.reanalyzed == 1
    record = w.bill(RCL).analysis
    assert record is not None and record.text_source == "documents"
    assert w.publisher.updates  # the thread is told the draft is out
    bill, change, reply_to = w.publisher.updates[0]
    assert (bill.number, reply_to) == (RCL, w.card_id(WYKAZ))
    assert change.content_changed


def test_a_plan_the_model_rejected_leaves_its_project_the_normal_path() -> None:
    w = World(llm_script={WYKAZ: make_analysis(relevant=False, score=1)})
    w.add_wykaz_entry()
    w.run()

    w.add_rcl_project(rcl_project(wykaz_number="UD408"))
    report = w.run()

    # No card to inherit, but the project is still the plan's: it keeps the thread's number and
    # is analysed from its documents, which is what decides whether it gets a card of its own.
    assert w.publication(WYKAZ, PublicationKind.NEW_BILL) is None
    assert w.bill(RCL).analysis is not None
    assert w.bill(RCL).analysis.text_source == "documents"  # type: ignore[union-attr]
    assert report.published == 0  # the link happens in tracking, after this run's publishing
    assert w.run().published == 1
    assert w.publisher.new_bills[0][0].number == RCL


def test_a_project_under_a_number_we_do_not_follow_is_discovered_as_usual() -> None:
    w = World()
    w.add_wykaz_entry(entry=wykaz_entry(number="UD999"))
    w.run()

    w.add_rcl_project(rcl_project(wykaz_number="UC164"))
    report = w.run()

    assert report.rcl_discovered == 1
    assert w.publication(RCL) is not None

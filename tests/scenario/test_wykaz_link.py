"""A plan's project appears on RCL: one thread, one card, and the text is finally read."""

import datetime as dt

from lexinform.models import BillStatus, PublicationKind, PublicationStatus
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


def test_a_plan_claims_a_project_the_listing_no_longer_shows_as_changed() -> None:
    """The listing shows a project when it moves, and RCL discovery is what hands it to the plan
    — so a project published before the plan was followed, or quiet since, would never reach it
    and the card would go on saying there is no text. The numbers RCL has published are written
    down for every row of the listing (`index-rcl-numbers` fills in the years before the bot),
    and the plan claims its own in tracking, whatever the listing shows today.
    """
    w = World()
    w.add_wykaz_entry()
    w.run()
    card = w.card_id(WYKAZ)
    project = rcl_project(wykaz_number="UD408", created=dt.date(2026, 9, 2))
    w.add_rcl_project(project)
    w.rcl.listing.clear()  # it has not moved since; the walk of this run will not show it
    w.repo.remember_rcl_wykaz_number("UD408", project.id, project.created)

    report = w.run()

    assert report.linked == 1
    inherited = w.publication(RCL)
    assert inherited is not None and inherited.message_id == card
    assert len(w.publisher.new_bills) == 1  # no second card for the same bill
    assert w.bill(WYKAZ).status is BillStatus.LINKED


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
    record = w.bill(RCL).analysis
    assert record is not None and record.text_source == "documents"
    assert report.published == 0  # the link happens in tracking, after this run's publishing
    assert w.run().published == 1
    assert w.publisher.new_bills[0][0].number == RCL


def test_a_reply_waits_for_a_still_unsent_plan_card_instead_of_replying_to_nothing() -> None:
    """A card `failed` but still retriable is not `sent`: with nothing to inherit, the project
    is not told through a reply to it either (BUGS.md #16) — it falls to the normal publish path
    instead, the way a plan the model rejected already does (`_NO_CARD_YET`)."""
    w = World(fail_publish={WYKAZ})
    w.add_wykaz_entry()
    w.run()
    plan_card = w.publication(WYKAZ)
    assert plan_card is not None and plan_card.status is PublicationStatus.FAILED

    w.add_rcl_project(rcl_project(wykaz_number="UD408"))
    report = w.run()

    assert report.linked == 1
    assert w.publisher.updates == []
    assert w.publication(RCL) is None  # no alias either: nothing to inherit yet
    assert w.bill(WYKAZ).status is BillStatus.LINKED

    assert w.run().published == 1  # a normal candidate now, per `_NO_CARD_YET`
    assert w.publisher.new_bills[-1][0].number == RCL


def test_a_project_under_a_number_we_do_not_follow_is_discovered_as_usual() -> None:
    w = World()
    w.add_wykaz_entry(entry=wykaz_entry(number="UD999"))
    w.run()

    w.add_rcl_project(rcl_project(wykaz_number="UC164"))
    report = w.run()

    assert report.rcl_discovered == 1
    assert w.publication(RCL) is not None

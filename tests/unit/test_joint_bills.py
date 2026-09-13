"""Bills the Sejm considers jointly share one thread: the first published gets the card, the
others a short "alternative bill" reply under it, and the group is followed through the card."""

import datetime as dt

from lexinform.models import (
    BillStatus,
    Committee,
    CommitteeSitting,
    PublicationKind,
    PublicationStatus,
)
from tests.fakes import make_analysis
from tests.harness import COMMITTEE_STAGES, World

DEPUTIES = "Poselski projekt ustawy o cudzoziemcach"
GOVERNMENT = "Rządowy projekt ustawy o cudzoziemcach"
LATER = dt.datetime(2026, 9, 8, 9, 0)


def _joint(w: World, number: str, title: str, *partners: str) -> None:
    """A print in /processes that the Sejm considers jointly with `partners`."""
    w.add_bill(number, title)
    w.touch(number, LATER, prints_considered_jointly=partners)


def test_a_later_joint_print_replies_under_the_existing_card_instead_of_a_card() -> None:
    w = World()
    w.add_bill("1933", DEPUTIES)
    w.run()
    _joint(w, "1929", GOVERNMENT, "1933", "316")
    w.touch("1933", LATER, prints_considered_jointly=("1929", "316"))

    report = w.run()
    again = w.run()

    assert (report.published, report.joint_published) == (0, 1)
    assert [b.number for b, _ in w.publisher.new_bills] == ["1933"]
    assert [(b.number, p.number, reply_to) for b, p, reply_to in w.publisher.joint_bills] == [
        ("1929", "1933", w.card_id("1933"))
    ]
    joined = w.publication("1929", PublicationKind.JOINT_BILL)
    assert joined is not None and joined.status is PublicationStatus.SENT
    assert w.publication("1929") is None  # no card of its own
    assert (again.published, again.joint_published) == (0, 0)


def test_the_government_print_gets_the_card_when_the_group_arrives_in_one_run() -> None:
    w = World()
    _joint(w, "1929", GOVERNMENT, "1933")
    _joint(w, "1933", DEPUTIES, "1929")
    w.touch("1933", LATER, document_date=dt.date(2026, 8, 1))  # would sort first on its own

    report = w.run()

    assert (report.published, report.joint_published) == (1, 1)
    assert [b.number for b, _ in w.publisher.new_bills] == ["1929"]
    assert [(b.number, p.number) for b, p, _ in w.publisher.joint_bills] == [("1933", "1929")]


def test_a_failed_joint_reply_is_retried_and_never_becomes_a_card() -> None:
    w = World(fail_publish={"1929"})
    w.add_bill("1933", DEPUTIES)
    w.run()
    _joint(w, "1929", GOVERNMENT, "1933")

    failed = w.run()
    w.publisher.fail_on = set()
    retried = w.run()

    assert failed.joint_published == 0 and failed.errors
    assert retried.joint_published == 1
    joined = w.publication("1929", PublicationKind.JOINT_BILL)
    assert joined is not None and joined.status is PublicationStatus.SENT
    assert [b.number for b, _ in w.publisher.new_bills] == ["1933"]


def test_the_group_is_followed_through_the_card_bill_only() -> None:
    w = World()
    w.add_bill("1933", DEPUTIES)
    w.run()
    _joint(w, "1929", GOVERNMENT, "1933")
    w.run()
    w.set_stages("1933", COMMITTEE_STAGES)
    w.set_stages("1929", COMMITTEE_STAGES)
    w.clock.advance(days=1)

    w.run()

    assert [(b.number, reply_to) for b, _, reply_to in w.publisher.updates] == [
        ("1933", w.card_id("1933"))
    ]


def test_a_joint_print_gets_its_own_card_when_the_partner_was_withdrawn() -> None:
    w = World()
    w.add_bill("1933", DEPUTIES)
    w.run()
    w.touch("1933", LATER, closure_date=dt.date(2026, 9, 7), passed=False)
    _joint(w, "1929", GOVERNMENT, "1933")

    report = w.run()

    assert (report.published, report.joint_published) == (1, 0)
    assert [b.number for b, _ in w.publisher.new_bills] == ["1933", "1929"]


def test_one_sitting_is_told_once_for_the_whole_group() -> None:
    """A committee takes the group together, so a post per print is the same news twice — which
    is what druki 1929 and 1933 did in the channel."""
    w = World()
    w.gateway.committees["ASW"] = Committee(term=10, code="ASW", name="Komisja ASW")
    for number, other in (("3039", "3040"), ("3040", "3039")):
        w.add_bill(number, f"Projekt ustawy o cudzoziemcach {number}", stages=COMMITTEE_STAGES)
        w.touch(number, dt.datetime(2026, 9, 7, 9, 0), prints_considered_jointly=(other,))
    w.gateway.committee_sittings["ASW"] = (
        CommitteeSitting(
            code="ASW",
            num=136,
            date=dt.date(2026, 9, 17),
            status="PLANNED",
            agenda='<div class="agenda-indent-0">Rozpatrzenie druków nr 3039 i 3040</div>',
        ),
    )

    report = w.run()

    assert report.agenda_posted == 1
    assert [item.ref for _, item, _ in w.publisher.agendas] == ["ASW/136/2026-09-17"]


def test_the_reply_names_the_thread_and_carries_both_tags() -> None:
    """The reply is rendered by the production publisher, so what the group's followers read is
    what a test reads: which druk the thread belongs to, and a tag for each print in it."""
    w = World()
    w.add_bill("1933", DEPUTIES)
    w.run()
    _joint(w, "1929", GOVERNMENT, "1933", "316")

    w.run()

    (text,) = w.publisher.texts(PublicationKind.JOINT_BILL)
    assert "Альтернативный проект того же закона" in text
    assert "druk 1933, 316" in text
    assert text.endswith("#kadencja10druk1929 #важность5 #легализация #kadencja10druk1933")


def test_the_print_that_replies_is_never_sent_to_the_model() -> None:
    """The reply carries the card's verdict, its tags and its next step, never one of its own, so
    an analysis of the print it is under would be paid for and shown to nobody. Druk 1933 cost
    305,132 input tokens that way — a seventh of everything the project had spent."""
    w = World()
    w.add_bill("1933", DEPUTIES)
    w.run()
    _joint(w, "1929", GOVERNMENT, "1933", "316")

    report = w.run()

    assert [ctx.number for ctx in w.llm.contexts] == ["1933"]
    assert (report.analyzed, report.analysis_skipped_joint) == (0, 1)
    assert w.bill("1929").status is BillStatus.SKIPPED_JOINT
    assert report.joint_published == 1


def test_a_print_left_unanalysed_is_analysed_when_the_group_loses_its_card() -> None:
    """A print is left unanalysed because the reply it will get carries the card's verdict. If
    the print holding that card is withdrawn before the reply goes out, there is no verdict to
    carry any more and no card to hang under: this print needs an analysis of its own.
    """
    w = World(fail_publish={"1929"})
    w.add_bill("1933", DEPUTIES)
    w.run()
    _joint(w, "1929", GOVERNMENT, "1933")
    w.run()  # the reply fails to send, so the print is still waiting for its post
    assert w.bill("1929").status is BillStatus.SKIPPED_JOINT
    w.publisher.fail_on.clear()
    w.touch("1933", LATER, closure_date=dt.date(2026, 9, 9), passed=False)

    w.run()  # publishing finds no card to reply under and asks for an analysis instead
    assert w.bill("1929").status is BillStatus.ANALYSIS_PENDING
    report = w.run()

    assert [ctx.number for ctx in w.llm.contexts] == ["1933", "1929"]
    assert w.bill("1929").analysis is not None
    assert report.published == 1


def test_a_joint_print_whose_partner_has_no_card_is_analysed_as_usual() -> None:
    """The skip is about the card, not about the group: a print considered jointly with one that
    the channel never posted has nobody to reply under and is judged on its own."""
    w = World(llm_script={"1933": make_analysis(relevant=False)})
    w.add_bill("1933", DEPUTIES)
    w.run()
    assert w.publication("1933") is None
    _joint(w, "1929", GOVERNMENT, "1933")

    report = w.run()

    assert [ctx.number for ctx in w.llm.contexts] == ["1933", "1929"]
    assert (report.published, report.analysis_skipped_joint) == (1, 0)

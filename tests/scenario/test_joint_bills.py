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
from tests.fakes import make_analysis, make_comparison
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
    assert [item.ref for _, item, _ in w.publisher.agendas] == [
        "ASW/136/2026-09-17"
    ]  # no hour, no room: the stamp is the day alone


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


def test_the_print_that_replies_is_analysed_and_told_apart_from_the_card() -> None:
    """The reply is not a second card, but it is not a bare name either: the print is read like
    any other and the reply says how it differs from the one the reader already knows about."""
    w = World()
    w.add_bill("1933", DEPUTIES)
    w.run()
    _joint(w, "1929", GOVERNMENT, "1933", "316")

    report = w.run()

    assert [ctx.number for ctx in w.llm.contexts] == ["1933", "1929"]
    assert (report.analyzed, report.joint_published) == (1, 1)
    assert [(c.subject.number, [o.number for o in c.others]) for c in w.llm.joint_contexts] == [
        ("1929", ["1933"])
    ]
    joint = w.bill("1929").joint
    assert joint is not None and joint.compared_with == ["1933"]


def test_the_comparison_is_made_of_what_the_channel_says_not_of_the_texts() -> None:
    """The model is given the descriptions of both bills and no text at all: every print of the
    group has been read once by its own analysis, and reading them again to spot the difference
    would cost a second full reading of each."""
    w = World()
    w.add_bill("1933", DEPUTIES)
    w.run()
    _joint(w, "1929", GOVERNMENT, "1933")

    w.run()

    (ctx,) = w.llm.joint_contexts
    (other,) = ctx.others
    assert other.number == "1933"
    assert other.summary == w.bill("1933").analysis.analysis.summary  # type: ignore[union-attr]
    assert ctx.subject.summary == w.bill("1929").analysis.analysis.summary  # type: ignore[union-attr]


def test_the_reply_says_what_the_bill_does_and_how_it_differs() -> None:
    w = World(joint_script={"1929": make_comparison(differences=["Срок 5 лет вместо 3"])})
    w.add_bill("1933", DEPUTIES)
    w.run()
    _joint(w, "1929", GOVERNMENT, "1933")

    w.run()

    (text,) = w.publisher.texts(PublicationKind.JOINT_BILL)
    assert "Чем отличается от других проектов" in text
    assert "Срок 5 лет вместо 3" in text
    assert "Проект распространяет льготу и на студентов." in text


def test_a_print_that_says_the_same_thing_is_told_so() -> None:
    """The commonest answer: two prints on one subject that differ in wording alone. Saying it
    is as much news for a reader as a list of differences."""
    w = World(joint_script={"1929": make_comparison(same_substance=True, differences=[])})
    w.add_bill("1933", DEPUTIES)
    w.run()
    _joint(w, "1929", GOVERNMENT, "1933")

    w.run()

    (text,) = w.publisher.texts(PublicationKind.JOINT_BILL)
    assert "По сути то же самое" in text
    assert "Чем отличается" not in text


def test_a_comparison_the_model_refused_still_leaves_the_reply() -> None:
    """The comparison is an embellishment of the reply and may never stop it: the model was down
    and the reader still gets the print, its thread and its links."""
    w = World(joint_script={"1929": RuntimeError("no")})
    w.add_bill("1933", DEPUTIES)
    w.run()
    _joint(w, "1929", GOVERNMENT, "1933")

    report = w.run()

    assert report.joint_published == 1
    assert w.bill("1929").joint is None
    (text,) = w.publisher.texts(PublicationKind.JOINT_BILL)
    assert "Альтернативный проект того же закона" in text
    assert "Чем отличается" not in text


def test_the_comparison_is_asked_once_and_kept() -> None:
    w = World()
    w.add_bill("1933", DEPUTIES)
    w.run()
    _joint(w, "1929", GOVERNMENT, "1933")
    w.run()

    w.run()

    assert len(w.llm.joint_contexts) == 1


def test_a_joint_print_is_not_triaged_when_the_group_already_holds_the_card() -> None:
    """The card has answered the triage's own question, of a bill on the same subject and before
    the same committee. What the cheap pass can still do here is say a confident "no" and take
    an alternative bill out of the channel for the price of a call it almost never saves."""
    w = World(triage=True)
    w.add_bill("1933", DEPUTIES)
    w.run()
    assert [ctx.number for ctx in w.llm.triage_contexts] == ["1933"]
    _joint(w, "1929", GOVERNMENT, "1933")

    w.run()

    assert [ctx.number for ctx in w.llm.triage_contexts] == ["1933"]
    assert [ctx.number for ctx in w.llm.contexts] == ["1933", "1929"]


def test_a_joint_print_gets_a_card_of_its_own_when_the_group_loses_its_card() -> None:
    """The print was analysed on its own text, so when the print holding the card is withdrawn
    before the reply goes out there is a verdict to make a card of, and no second reading."""
    w = World(fail_publish={"1929"})
    w.add_bill("1933", DEPUTIES)
    w.run()
    _joint(w, "1929", GOVERNMENT, "1933")
    w.run()  # the reply fails to send, so the print is still waiting for its post
    w.publisher.fail_on.clear()
    w.touch("1933", LATER, closure_date=dt.date(2026, 9, 9), passed=False)

    report = w.run()

    assert [ctx.number for ctx in w.llm.contexts] == ["1933", "1929"]
    assert report.published == 1
    assert [b.number for b, _ in w.publisher.new_bills] == ["1933", "1929"]


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
    assert (report.published, report.joint_published) == (1, 0)
    assert w.llm.joint_contexts == []


def test_a_print_the_keywords_missed_is_read_when_its_group_has_a_card() -> None:
    """The keywords are a guess about a text; that the committee works on this print together
    with one the channel has carded is a fact the Sejm states. Over term 10 eight groups have a
    print the prefilter drops beside one it keeps, and in all eight it is the same bill by
    another applicant — druk 1426 is the government's Kodeks pracy against the deputies' 1404.
    """
    w = World(text_prefilter=False)  # the title is the only gate, so a miss is a miss
    w.add_bill("1404", DEPUTIES)
    w.run()
    w.add_bill("1426", "Rządowy projekt ustawy o zmianie ustawy - Kodeks pracy")
    w.run()
    assert w.bill("1426").status is BillStatus.SKIPPED_PREFILTER

    w.touch("1426", LATER, prints_considered_jointly=("1404",))
    report = w.run()

    assert report.joint_revived == 1
    assert [ctx.number for ctx in w.llm.contexts] == ["1404", "1426"]
    assert report.joint_published == 1


def test_a_skipped_print_stays_skipped_while_its_group_has_no_card() -> None:
    """The revival is about the card, not about the group: nothing in the channel is waiting for
    this bill, so the keywords' "no" stands and the run spends nothing."""
    w = World(text_prefilter=False, llm_script={"1404": make_analysis(relevant=False)})
    w.add_bill("1404", DEPUTIES)
    w.add_bill("1426", "Rządowy projekt ustawy o zmianie ustawy - Kodeks pracy")
    w.run()
    w.touch("1426", LATER, prints_considered_jointly=("1404",))

    report = w.run()

    assert report.joint_revived == 0
    assert w.bill("1426").status is BillStatus.SKIPPED_PREFILTER
    assert [ctx.number for ctx in w.llm.contexts] == ["1404"]


def test_a_reply_goes_into_the_thread_even_under_the_score_threshold() -> None:
    """The bar decides whether a bill is worth a card in everyone's feed. A reply is a message in
    a thread its readers chose, and by then the print has been read and judged: dropping it under
    the bar would mean paying for the reading and throwing the answer away."""
    w = World(llm_script={"1929": make_analysis(score=2)})
    w.add_bill("1933", DEPUTIES)
    w.run()
    _joint(w, "1929", GOVERNMENT, "1933")

    report = w.run()

    assert (report.published, report.joint_published) == (0, 1)
    assert [(b.number, p.number) for b, p, _ in w.publisher.joint_bills] == [("1929", "1933")]


def test_a_print_under_the_threshold_with_no_card_to_reply_under_stays_silent() -> None:
    """The bar is lifted for the thread, not for the bill: a print whose group has no card would
    be a card of its own, and under the threshold the channel does not want one."""
    w = World(llm_script={"1933": make_analysis(relevant=False), "1929": make_analysis(score=2)})
    w.add_bill("1933", DEPUTIES)
    w.run()
    _joint(w, "1929", GOVERNMENT, "1933")

    report = w.run()

    assert (report.published, report.joint_published) == (0, 0)
    assert w.publication("1929") is None

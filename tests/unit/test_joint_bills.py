"""Bills the Sejm considers jointly share one thread: the first published gets the card, the
others a short "alternative bill" reply under it, and the group is followed through the card."""

import datetime as dt

from lexinform.models import PublicationKind, PublicationStatus
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

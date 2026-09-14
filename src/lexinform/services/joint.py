"""Which of the prints considered jointly with a bill already carries the group's card.

The Sejm considers several prints on the same subject together: one committee report for all of
them, their stages coinciding from the joint referral on. The channel gives such a group one
card and answers under it for every later print, so only one of the group's prints is ever the
one a reader reads.

Both the publisher and the analysis need that answer, for different reasons: the publisher to
send a reply instead of a card, the analysis to leave the cheap pass out — the card has already
said the subject matters, and a confident "no" from a screening model would take an alternative
bill out of the channel for the price of one call it almost never saves.

The reply is not a second card and not a second verdict: it says how this print differs from the
ones the reader has already read about (`group_of` gathers them, `AnalysisService.compare_joint`
asks). Until 2026-09-14 it said nothing at all beyond the title and the links, and a reader
meeting «Альтернативный проект того же закона» had no way of telling whether it was the same
bill in other words or a different answer to the same question.
"""

import logging

from lexinform.models import Bill, BillStatus, PublicationKind, PublicationStatus
from lexinform.ports import BillRepository

log = logging.getLogger(__name__)


def primary_of(repo: BillRepository, bill: Bill, channel_id: str) -> tuple[Bill, int] | None:
    """The bill `bill` is considered jointly with that already has a card in this channel and is
    still followed, with the card's message id; None when `bill` gets its own card.

    A print that was withdrawn or rejected is not one of them: its thread is over, and the bill
    gets a card of its own.
    """
    for number in bill.summary.prints_considered_jointly:
        card = repo.get_publication(bill.term, number, PublicationKind.NEW_BILL, channel_id)
        if card is None or card.status is not PublicationStatus.SENT or card.message_id is None:
            continue
        other = repo.get(bill.term, number)
        if other is None or other.discontinued_at is not None:
            continue
        if other.summary.closure_date is not None and not other.summary.passed:
            continue
        return other, card.message_id
    return None


def revive_prefilter_skips(repo: BillRepository, channel_id: str) -> list[str]:
    """Prints a prefilter skipped whose group already holds a card, put back in the queue.

    The keywords are a guess about a text; that a committee is working on this print together
    with one the channel has carded is a fact the Sejm states. Measured over term 10: eight
    groups have a print the prefilter drops beside one it keeps, and in all eight it is the same
    bill by another applicant — druk 1426 is the government's Kodeks pracy against the deputies'
    1404, druk 316 the President's asystencja osobista beside druki 1929 and 1933, druk 2530 the
    same rynek kryptoaktywów as 2529. Reading every one of them costs **$0.71 for the whole
    term**, and each is one reply in a thread whose readers are waiting for exactly that bill.

    The same reason as the triage, one gate earlier: what the prefilter can still do here is drop
    an alternative bill for want of a keyword the card's own text had. Order does not matter —
    the print may be skipped before the card exists or discovered long after it — because every
    run asks the question again of every skipped row that names a group, and `/processes` carries
    `printsConsideredJointly` in the listing, so even a row that was never read has its group.
    """
    revived = []
    for bill in repo.list_skipped_with_joint_prints():
        primary = primary_of(repo, bill, channel_id)
        if primary is None:
            continue
        other, _ = primary
        repo.set_status(
            bill.term,
            bill.number,
            BillStatus.ANALYSIS_PENDING,
            reason=(
                f"considered jointly with druk {other.number}, which the channel carries:"
                " the keywords do not decide an alternative bill"
            ),
        )
        log.info(
            "%s was skipped by the prefilter but is considered jointly with %s, which has a card",
            bill.number,
            other.number,
        )
        revived.append(bill.number)
    return revived


def group_of(repo: BillRepository, bill: Bill) -> list[Bill]:
    """The prints considered jointly with `bill` that the channel has an analysis of, in the
    order the Sejm names them: what a comparison can be made against.

    A print with no analysis of its own is left out rather than described from its title: the
    comparison is of what the channel says about the bills, and about that one it says nothing
    yet. When it is analysed, `compared_with` no longer matches and the group is compared again.
    """
    others = []
    for number in bill.summary.prints_considered_jointly:
        other = repo.get(bill.term, number)
        if other is not None and other.analysis is not None:
            others.append(other)
    return others

"""Which of the prints considered jointly with a bill already carries the group's card.

The Sejm considers several prints on the same subject together, their stages coinciding from the
joint referral on, and the channel gives such a group one card with a reply under it for every
later print. The publisher needs that answer to send a reply instead of a card, and the analysis
to leave the cheap pass out: the card has already said the subject matters.

The reply is neither a second card nor a second verdict — it says how this print differs from the
ones the reader knows (`group_of` gathers them, `AnalysisService.compare_joint` asks).
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

    The keywords guess about a text; that a committee works on this print together with a carded
    one is a fact the Sejm states. Over term 10 eight groups have a print the prefilter drops
    beside one it keeps, and in all eight it is the same bill by another applicant; reading them
    all costs $0.71 for the term. Order does not matter — every run asks again of every skipped
    row naming a group, and `printsConsideredJointly` is in the listing, so a row that was never
    read still knows its group.
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

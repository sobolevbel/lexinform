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

from lexinform.models import Bill, PublicationKind, PublicationStatus
from lexinform.ports import BillRepository


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

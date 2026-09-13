"""Which of the prints considered jointly with a bill already carries the group's card.

The Sejm considers several prints on the same subject together: one committee report for all of
them, their stages coinciding from the joint referral on. The channel gives such a group one
card and answers under it for every later print, so only one of the group's prints is ever the
one a reader reads.

Both the publisher and the analysis need that answer, and for the same reason: the publisher to
send a reply instead of a card, the analysis not to pay for a judgement that will never be
shown. Druk 1933 cost 305,132 input tokens — $1.53, a seventh of everything the project had
spent — for an analysis that went out as a `joint_bill` reply, which by design carries none.
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

"""Who signed a Sejm bill: the cover letter of the print matched against the MP directory."""

import logging

from lexinform.authors import MpDirectory, parse_cover_letter
from lexinform.errors import ServiceUnavailableError
from lexinform.models import ApplicantType, Bill, BillAuthors
from lexinform.ports import SejmGateway

log = logging.getLogger(__name__)

_SIGNED_BILLS = frozenset({ApplicantType.DEPUTIES, ApplicantType.COMMITTEE})


class SejmAuthorsResolver:
    """Signatories (deputies' bills) or the representative (committee bills); the MP directory is
    fetched once per process."""

    def __init__(self, gateway: SejmGateway) -> None:
        self._gateway = gateway
        self._mps: MpDirectory | None = None

    def resolve(self, bill: Bill, text: str) -> BillAuthors | None:
        if bill.summary.applicant_type not in _SIGNED_BILLS:
            return None
        try:
            letter = parse_cover_letter(text)
            if not letter.signatories and not letter.representative:
                return None
            if self._mps is None:
                self._mps = MpDirectory.from_mps(self._gateway.list_mps(bill.term))
            return self._mps.resolve(letter)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("authors of druk %s not resolved: %s", bill.number, exc)
            return None

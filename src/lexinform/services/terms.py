"""Which Sejm term (kadencja) the bot works in.

Print numbers restart with every term, so every API path and every row carries the term. The
running term comes from `/sejm/term` (the item flagged `current`), so that the bot moves to a new
Sejm by itself; `LEXINFORM_TERM` pins one instead. When the API is down the newest term the
database knows is used, so that tracking and reminders still run; an empty database and no API
leaves nothing to do, and the error propagates.
"""

import logging

from lexinform.errors import ServiceUnavailableError
from lexinform.models import current_term
from lexinform.ports import BillRepository, SejmGateway

log = logging.getLogger(__name__)


class TermResolver:
    def __init__(
        self, gateway: SejmGateway, repo: BillRepository, *, pinned: int | None = None
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._pinned = pinned
        self._resolved: int | None = None

    def current(self) -> int:
        """The pinned term, else the API's current one (asked once per process), else the newest
        term in the database. Raises the API's `ServiceUnavailableError` when none is known."""
        if self._pinned is not None:
            return self._pinned
        if self._resolved is None:
            self._resolved = self._resolve()
        return self._resolved

    def _resolve(self) -> int:
        try:
            term = current_term(self._gateway.list_terms())
        except ServiceUnavailableError as exc:
            known = self._repo.known_terms()
            if not known:
                raise
            term = max(known)
            log.warning("%s; using the newest term in the database (%d)", exc.describe(), term)
            return term
        if term is None:
            raise RuntimeError("the Sejm API lists no term")
        return term

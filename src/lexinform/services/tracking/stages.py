"""Stage-level helpers: enrichment from other endpoints and the dedupe key of a change."""

import hashlib
import logging
from collections.abc import Iterable

from lexinform.errors import ServiceUnavailableError
from lexinform.models import Bill, Stage, aggregate_clubs
from lexinform.ports import SejmGateway

log = logging.getLogger(__name__)


class StageEnricher:
    """Adds what the stage tree does not carry: per-club votes and committee names.

    Best effort: a failure here degrades the update, it never blocks it (except an outage).
    """

    def __init__(self, gateway: SejmGateway, *, club_breakdown: bool = True) -> None:
        self._gateway = gateway
        self._club_breakdown = club_breakdown
        self._committee_names: dict[str, str] = {}

    def enrich(self, term: int, stage: Stage) -> Stage:
        try:
            if (
                self._club_breakdown
                and stage.stage_type == "Voting"
                and stage.voting is not None
                and not stage.voting.clubs
                and stage.voting.sitting is not None
                and stage.voting.voting_number is not None
            ):
                votes = self._gateway.get_voting(
                    term, stage.voting.sitting, stage.voting.voting_number
                )
                voting = stage.voting.model_copy(update={"clubs": aggregate_clubs(votes)})
                return stage.model_copy(update={"voting": voting})
            if stage.stage_type == "Referral" and stage.committee_code and not stage.committee_name:
                name = self.committee_name(term, stage.committee_code)
                return stage.model_copy(update={"committee_name": name})
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("could not enrich stage %s: %s", stage.stage_name, exc)
        return stage

    def committee_name(self, term: int, code: str) -> str:
        """The committee's full name, fetched once per run."""
        if code not in self._committee_names:
            self._committee_names[code] = self._gateway.get_committee(term, code).name
        return self._committee_names[code]


def change_key(stage_fp: str, bill: Bill, *, closed: bool, supplements: Iterable[str] = ()) -> str:
    """Dedupe key for status_changes: stages, analysed text revision, closure announcement and
    the documents filed to the print. A document that arrives between two stages moves nothing
    else, so without its number the row would collide with the previous change and be dropped."""
    revision = bill.analysis.revision if bill.analysis else 0
    source = bill.analysis.source_url if bill.analysis else ""
    key = f"{stage_fp}|{revision}|{source}" + ("|closed" if closed else "")
    filed = ",".join(sorted(supplements))
    if filed:
        key = f"{key}|{filed}"
    return hashlib.sha256(key.encode()).hexdigest()

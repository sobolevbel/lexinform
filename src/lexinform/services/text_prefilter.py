"""Second prefilter stage: keyword search inside the print PDF for bills whose title said nothing.

Bills like "o zmianie niektórych ustaw w związku z ..." hide their scope in the text. Downloading
the PDF costs bandwidth, not tokens, so every title miss gets its text scanned; only bills with
enough distinct topics or repeated mentions go on to the (paid) LLM analysis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from lexinform.errors import ServiceUnavailableError
from lexinform.keywords import KeywordPrefilter, accept_text_hits
from lexinform.models import Bill, BillStatus
from lexinform.ports import BillRepository, SejmGateway
from lexinform.services.documents import PdfTextLoader

log = logging.getLogger(__name__)

TEXT_HIT_PREFIX = "text:"


@dataclass
class TextPrefilterResult:
    checked: int = 0
    hits: int = 0
    failed: int = 0
    fatal_error: str | None = None


class TextPrefilterService:
    def __init__(
        self,
        gateway: SejmGateway,
        repo: BillRepository,
        loader: PdfTextLoader,
        prefilter: KeywordPrefilter,
        *,
        min_distinct: int = 2,
        min_occurrences: int = 3,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._loader = loader
        self._prefilter = prefilter
        self._min_distinct = min_distinct
        self._min_occurrences = min_occurrences

    def run(self, term: int, *, limit: int) -> TextPrefilterResult:
        result = TextPrefilterResult()
        if limit <= 0:
            return result
        for bill in self._repo.list_by_status(
            term, [BillStatus.TEXT_PREFILTER_PENDING], limit=limit
        ):
            result.checked += 1
            try:
                if self.check(bill):
                    result.hits += 1
            except ServiceUnavailableError as exc:
                # Bills stay pending and are checked on the next run.
                result.fatal_error = exc.describe()
                log.error("aborting text prefilter phase: %s", result.fatal_error)
                break
            except Exception as exc:
                result.failed += 1
                log.warning("text prefilter for druk %s failed: %s", bill.number, exc)
                self._repo.set_status(
                    bill.term, bill.number, BillStatus.SKIPPED_TEXT_PREFILTER, prefilter_hits=[]
                )
        log.info(
            "text prefilter: checked=%d hits=%d failed=%d",
            result.checked,
            result.hits,
            result.failed,
        )
        return result

    def check(self, bill: Bill) -> bool:
        """Scan the main print PDF; True when the bill is sent on to analysis."""
        text = self._load(bill)
        counts = self._prefilter.match_counts(text) if text else {}
        accepted = accept_text_hits(
            counts, min_distinct=self._min_distinct, min_occurrences=self._min_occurrences
        )
        hits = [f"{TEXT_HIT_PREFIX}{name}" for name in counts]
        status = BillStatus.ANALYSIS_PENDING if accepted else BillStatus.SKIPPED_TEXT_PREFILTER
        self._repo.set_status(bill.term, bill.number, status, prefilter_hits=hits)
        if accepted:
            log.info(
                "candidate druk %s by text (%s): %s",
                bill.number,
                ", ".join(f"{k}×{v}" for k, v in counts.items()),
                bill.summary.title,
            )
        elif counts:
            log.info("druk %s: weak text hits only (%s)", bill.number, counts)
        return accepted

    def _load(self, bill: Bill) -> str | None:
        try:
            print_info = self._gateway.get_print(bill.term, bill.number)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("print %s unavailable for text prefilter: %s", bill.number, exc)
            return None
        pdf = print_info.main_pdf
        if pdf is None:
            log.info("druk %s has no PDF attachment; text prefilter skipped", bill.number)
            return None
        return self._loader.load(pdf.url)

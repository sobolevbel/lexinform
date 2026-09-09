"""Second prefilter stage: keyword search inside the print PDF for bills whose title said nothing.

Bills like "o zmianie niektórych ustaw w związku z ..." hide their scope in the text. Downloading
the PDF costs bandwidth, not tokens, so every title miss gets its text scanned; only bills with
enough distinct topics or repeated mentions go on to the (paid) LLM analysis.
"""

import logging
from dataclasses import dataclass

from lexinform.concurrency import fan_out
from lexinform.errors import ServiceUnavailableError
from lexinform.keywords import KeywordPrefilter, accept_text_hits
from lexinform.models import Bill, BillStatus
from lexinform.ports import BillRepository, TextSource
from lexinform.services.documents import TextLoader

log = logging.getLogger(__name__)

TEXT_HIT_PREFIX = "text:"


@dataclass
class TextPrefilterResult:
    """Counters of one text-prefilter phase."""

    checked: int = 0
    hits: int = 0
    unreadable: int = 0  # no document, no text layer, or the download/extraction failed
    failed: int = 0
    fatal_error: str | None = None


@dataclass(frozen=True)
class _Loaded:
    """What the prefilter has to scan: the text, or why there is none."""

    text: str | None
    problem: str | None


class TextPrefilterService:
    """Scans the print PDF of title misses and promotes strong keyword hits to analysis."""

    def __init__(
        self,
        repo: BillRepository,
        texts: TextSource,
        loader: TextLoader,
        prefilter: KeywordPrefilter,
        *,
        min_distinct: int = 2,
        min_occurrences: int = 3,
        workers: int = 1,
    ) -> None:
        self._repo = repo
        self._texts = texts
        self._loader = loader
        self._prefilter = prefilter
        self._min_distinct = min_distinct
        self._min_occurrences = min_occurrences
        self._workers = workers

    def run(self, *, limit: int) -> TextPrefilterResult:
        result = TextPrefilterResult()
        if limit <= 0:
            return result
        pending = self._repo.list_by_status([BillStatus.TEXT_PREFILTER_PENDING], limit=limit)
        for outcome in fan_out(pending, self._load, workers=self._workers):
            bill = outcome.item
            result.checked += 1
            try:
                loaded = outcome.result()
            except ServiceUnavailableError as exc:  # bills stay pending for the next run
                result.fatal_error = exc.describe()
                log.error("aborting text prefilter phase: %s", result.fatal_error)
                break
            except Exception as exc:
                # A 404, a damaged file, an unknown format: the bill is skipped with the reason
                # on record (`lexinform reprefilter --include-text-skipped` scans it again).
                result.failed += 1
                log.warning("text prefilter for druk %s failed: %s", bill.number, exc)
                loaded = _Loaded(None, f"text prefilter failed: {type(exc).__name__}: {exc}")
            if loaded.text is None:
                result.unreadable += 1
            if self._decide(bill, loaded):
                result.hits += 1
        log.info(
            "text prefilter: checked=%d hits=%d unreadable=%d failed=%d",
            result.checked,
            result.hits,
            result.unreadable,
            result.failed,
        )
        return result

    def check(self, bill: Bill) -> bool:
        """Scan the bill text; True when the bill is sent on to analysis."""
        return self._decide(bill, self._load(bill))

    def _decide(self, bill: Bill, loaded: _Loaded) -> bool:
        """Store the hits (weak ones too, for tuning), the bill's next status and, for a skip,
        the reason (`last_error`): a keyword miss and an unreadable file must stay apart."""
        counts = self._prefilter.match_counts(loaded.text) if loaded.text else {}
        accepted = accept_text_hits(
            counts, min_distinct=self._min_distinct, min_occurrences=self._min_occurrences
        )
        hits = [f"{TEXT_HIT_PREFIX}{name}" for name in counts]
        status = BillStatus.ANALYSIS_PENDING if accepted else BillStatus.SKIPPED_TEXT_PREFILTER
        reason = None
        if not accepted:
            reason = loaded.problem or (
                "text prefilter: weak hits only ("
                + ", ".join(f"{k}×{v}" for k, v in counts.items())
                + ")"
                if counts
                else "text prefilter: no keyword hits"
            )
        self._repo.set_status(bill.term, bill.number, status, prefilter_hits=hits, reason=reason)
        if accepted:
            log.info(
                "candidate druk %s by text (%s): %s",
                bill.number,
                ", ".join(f"{k}×{v}" for k, v in counts.items()),
                bill.summary.title,
            )
        else:
            log.info("druk %s skipped: %s", bill.number, reason)
        return accepted

    def _load(self, bill: Bill) -> _Loaded:
        """Network only. The text to scan, or the reason there is none; a download or extraction
        failure propagates for the caller to record (an outage aborts the phase)."""
        try:
            located = self._texts.locate(bill)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("text of %s unavailable for the prefilter: %s", bill.number, exc)
            return _Loaded(None, f"text prefilter: text unavailable: {type(exc).__name__}: {exc}")
        if located.document is None:
            log.info("%s has no readable text; text prefilter skipped", bill.number)
            return _Loaded(None, "text prefilter: no document to read")
        text = self._loader.load(located.document.url)  # the bill text alone decides
        if text is None:
            return _Loaded(None, "text prefilter: no text layer or file over the size limit")
        return _Loaded(text, None)

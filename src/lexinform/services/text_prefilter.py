"""Second prefilter stage: keyword search inside the print PDF for bills whose title said nothing.

Bills like "o zmianie niektórych ustaw w związku z ..." hide their scope in the text. Downloading
the PDF costs bandwidth, not tokens, so every title miss gets its text scanned; only bills with
enough distinct topics or repeated mentions go on to the (paid) LLM analysis.

A file with no text layer is the one case keywords cannot answer, and it is not a reason to drop
the bill: the model reads such a document as pages. Much of what the Sejm publishes is signed
paper, and it is the deputies' bills — the ones whose titles say "o zmianie niektórych ustaw"
most often — that arrive that way, so refusing them here was refusing them at both ends.
"""

import logging
from dataclasses import dataclass

from lexinform.concurrency import fan_out
from lexinform.errors import ServiceUnavailableError
from lexinform.keywords import WEAK_PATTERNS, KeywordPrefilter, accept_text_hits
from lexinform.models import Bill, BillStatus
from lexinform.ports import BillRepository, TextSource
from lexinform.services.documents import LoadedFile, TextLoader

log = logging.getLogger(__name__)

TEXT_HIT_PREFIX = "text:"


@dataclass
class TextPrefilterResult:
    """Counters of one text-prefilter phase.

    `scans` counts the bills sent on to the model because their file has pages but no text to
    search, `unreadable` those with no document, no pages either, or a download or extraction
    that failed.
    """

    checked: int = 0
    hits: int = 0
    scans: int = 0
    unreadable: int = 0
    failed: int = 0
    fatal_error: str | None = None


@dataclass(frozen=True)
class _Loaded:
    """What the prefilter has to scan: the text, or why there is none and what is there instead.

    `pages` is what the file holds when its text layer does not: a scan the model can still be
    shown. `problem` is the reason there is no text, for the bill's record.
    """

    text: str | None
    problem: str | None
    pages: int = 0

    @property
    def is_scan(self) -> bool:
        return self.text is None and self.pages > 0


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
            except ServiceUnavailableError as exc:
                result.fatal_error = exc.describe()
                log.error("aborting text prefilter phase: %s", result.fatal_error)
                break
            except Exception as exc:
                result.failed += 1
                log.warning("text prefilter for druk %s failed: %s", bill.number, exc)
                loaded = _Loaded(None, f"text prefilter failed: {type(exc).__name__}: {exc}")
            if loaded.is_scan:
                result.scans += 1
            elif loaded.text is None:
                result.unreadable += 1
            if self._decide(bill, loaded) and not loaded.is_scan:
                result.hits += 1
        log.info(
            "text prefilter: checked=%d hits=%d scans=%d unreadable=%d failed=%d",
            result.checked,
            result.hits,
            result.scans,
            result.unreadable,
            result.failed,
        )
        return result

    def check(self, bill: Bill) -> bool:
        """Scan the bill text; True when the bill is sent on to analysis."""
        return self._decide(bill, self._load(bill))

    def _decide(self, bill: Bill, loaded: _Loaded) -> bool:
        """Store the hits (weak ones too, for tuning), the bill's next status and, for a skip,
        the reason (`last_error`): a keyword miss and an unreadable file must stay apart.

        A file with pages and no text goes on to the model unsearched — there is nothing here
        that can read it, and the per-bill cost guard is what bounds the decision.
        """
        if loaded.is_scan:
            self._repo.set_status(
                bill.term,
                bill.number,
                BillStatus.ANALYSIS_PENDING,
                reason=f"text prefilter: no text layer, {loaded.pages} page(s) for the model",
            )
            log.info(
                "druk %s is %d scanned page(s); the model reads it: %s",
                bill.number,
                loaded.pages,
                bill.summary.title,
            )
            return True
        counts = self._prefilter.match_counts(loaded.text) if loaded.text else {}
        accepted = accept_text_hits(
            counts, min_distinct=self._min_distinct, min_occurrences=self._min_occurrences
        )
        hits = [f"{TEXT_HIT_PREFIX}{name}" for name in counts]
        status = BillStatus.ANALYSIS_PENDING if accepted else BillStatus.SKIPPED_TEXT_PREFILTER
        reason = None if accepted else loaded.problem or _miss(counts, self._min_distinct)
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
        """Network only. The bill text alone decides, so that is all this reads.

        The text to scan, or the reason there is none; a 404, a damaged file or an unknown format
        propagates for the caller to record — the bill is then skipped with the reason on file,
        and `lexinform reprefilter --include-text-skipped` scans it again. An outage aborts the
        phase instead, leaving the bills pending for the next run.
        """
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
        file = self._loader.read(located.document.url)
        if file.text is None:
            return _Loaded(None, _no_text(file), pages=file.pages)
        return _Loaded(file.text, None)


def _miss(counts: dict[str, int], min_distinct: int) -> str:
    """Why the keywords did not send the bill on — the actual reason, not "weak hits only" for
    every kind of miss: a single strong pattern that fell short of the threshold is not the same
    finding as a text that only names a border authority, and an operator reads these."""
    if not counts:
        return "text prefilter: no keyword hits"
    found = ", ".join(f"{name}×{n}" for name, n in counts.items())
    if all(name in WEAK_PATTERNS for name in counts):
        return f"text prefilter: weak patterns only ({found})"
    return f"text prefilter: under the threshold of {min_distinct} distinct patterns ({found})"


def _no_text(file: LoadedFile) -> str:
    """Why a file yielded no text: the two reasons are told apart because one of them
    (`oversize`) is ours to raise and the other is the paper the Sejm publishes."""
    if file.oversize:
        return "text prefilter: file over the download size limit"
    return "text prefilter: no text layer and no pages to read"

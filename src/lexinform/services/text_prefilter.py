"""Second prefilter stage: keyword search inside the print PDF for bills whose title said nothing.

Bills like "o zmianie niektórych ustaw w związku z ..." hide their scope in the text. Downloading
the PDF costs bandwidth, not tokens, so every title miss gets its text scanned; only bills with
enough distinct topics or repeated mentions go on to the (paid) LLM analysis.

A file with no text layer is the one case keywords cannot answer, and not a reason to drop the
bill: the model reads it as pages. The prints that arrive as scanned paper are the deputies'
bills, which are the ones whose titles say "o zmianie niektórych ustaw".
"""

import logging
from dataclasses import dataclass, field

from lexinform.concurrency import fan_out
from lexinform.errors import OrkaUnreachableError, ServiceUnavailableError
from lexinform.keywords import WEAK_PATTERNS, KeywordPrefilter, accept_text_hits
from lexinform.models import Bill, BillStatus
from lexinform.ports import BillRepository, TextSource
from lexinform.sections import carries_the_document
from lexinform.services.documents import MIN_TEXT_CHARS, LoadedFile, TextLoader

log = logging.getLogger(__name__)

TEXT_HIT_PREFIX = "text:"


@dataclass
class TextPrefilterResult:
    """Counters of one text-prefilter phase.

    `scans` counts the bills sent on to the model because their file has pages but no text to
    search, `unreadable` those with no document, no pages either, or a download or extraction
    that failed, and `unanswered` those whose host refused to hand the file over, which is not a
    finding about the bill and leaves it pending.
    """

    checked: int = 0
    rejected: list[str] = field(default_factory=list)
    hits: int = 0
    scans: int = 0
    unreadable: int = 0
    unanswered: int = 0
    failed: int = 0
    fatal_error: str | None = None


@dataclass(frozen=True)
class PrefilterLoad:
    """What the prefilter has to scan: the text, or why there is none and what is there instead.

    `pages` is what the file holds when its text layer does not: a scan the model can still be
    shown. `problem` is the reason there is no text, for the bill's record. `unanswered` says the
    host did not hand the file over at all, which is not a fact about the bill: the row keeps its
    pending status and the next run asks again.
    """

    text: str | None
    problem: str | None
    pages: int = 0
    unanswered: bool = False

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
        for outcome in fan_out(pending, self.load, workers=self._workers):
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
                loaded = self.problem(bill, exc)
            if loaded.unanswered:
                result.unanswered += 1
            elif loaded.is_scan:
                result.scans += 1
            elif loaded.text is None:
                result.unreadable += 1
            accepted = self.decide(bill, loaded)
            if accepted and not loaded.is_scan:
                result.hits += 1
            elif not accepted and loaded.text is not None:
                result.rejected.append(f"{bill.term}/{bill.number}")
        log.info(
            "text prefilter: checked=%d hits=%d scans=%d unreadable=%d unanswered=%d failed=%d",
            result.checked,
            result.hits,
            result.scans,
            result.unreadable,
            result.unanswered,
            result.failed,
        )
        return result

    def check(self, bill: Bill) -> bool:
        """Scan the bill text; True when the bill is sent on to analysis."""
        return self.decide(bill, self.load(bill))

    def problem(self, bill: Bill, exc: Exception) -> PrefilterLoad:
        """A per-bill failure as a load with its reason; an outage is the caller's to stop on."""
        if isinstance(exc, ServiceUnavailableError):
            raise exc
        if isinstance(exc, OrkaUnreachableError):
            # Orka's WAF refuses by address and by the day; only its 404 is about the bill.
            log.warning("text prefilter for druk %s: %s", bill.number, exc)
            return PrefilterLoad(
                None,
                f"text prefilter: {type(exc).__name__}: {exc}",
                unanswered=not exc.file_is_missing,
            )
        log.warning("text prefilter for druk %s failed: %s", bill.number, exc)
        return PrefilterLoad(None, f"text prefilter failed: {type(exc).__name__}: {exc}")

    def decide(self, bill: Bill, loaded: PrefilterLoad) -> bool:
        """Store the hits (weak ones too, for tuning), the bill's next status and, for a skip,
        the reason (`last_error`): a keyword miss and an unreadable file must stay apart.

        A file with pages and no text goes to the model unsearched, bounded by the per-bill cost
        guard. A file the host would not hand over is neither: the bill stays pending and the next
        run asks again.
        """
        if loaded.unanswered:
            self._repo.set_status(
                bill.term,
                bill.number,
                BillStatus.TEXT_PREFILTER_PENDING,
                reason=loaded.problem,
            )
            log.info("druk %s stays pending: %s", bill.number, loaded.problem)
            return False
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

    def load(self, bill: Bill) -> PrefilterLoad:
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
            return PrefilterLoad(
                None, f"text prefilter: text unavailable: {type(exc).__name__}: {exc}"
            )
        if located.document is None:
            if bill.has_process:
                # The Sejm lists a process before the print's file is attached to it. Druk 3094
                # was judged at 10:11 UTC on 15 Sept 2026 and its PDF appeared at 10:16, five
                # minutes later — by then the row said "no document to read", which is a verdict,
                # and the only way back was `reprefilter --include-text-skipped`. A file that is
                # not there yet is about the day, like the WAF's refusal above, not about the
                # bill; the next run asks again.
                log.info("print %s has no file yet; the prefilter asks again", bill.number)
                return PrefilterLoad(
                    None, "text prefilter: the print has no file yet", unanswered=True
                )
            log.info("%s has no readable text; text prefilter skipped", bill.number)
            return PrefilterLoad(None, "text prefilter: no document to read")
        file = self._loader.read(located.document.url)
        if file.text is None:
            return PrefilterLoad(None, _no_text(file), pages=file.pages)
        if not carries_the_document(file.text, min_chars=MIN_TEXT_CHARS, pages=file.pages):
            # The same question the analysis asks, and it has to be the same question. `TextLoader`
            # calls a file textless only under `MIN_TEXT_CHARS`, so a print whose text layer is
            # the letter that hands it to the Marshal — 700-1,200 characters — arrived here
            # looking like a document, was searched for keywords that a transmittal note never
            # contains, and was skipped. Measured over term 10 (14 Sept 2026): **91 of the 938
            # prints** are that case, every one with pages the model could have read, and the
            # invariant says a file keywords cannot search is not a file to drop.
            return PrefilterLoad(None, _no_document(file), pages=file.pages)
        return PrefilterLoad(file.text, None)


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


def _no_document(file: LoadedFile) -> str:
    """Why the text that was there is not the document: the covering letter, or an OCR layer too
    thin for the paper it came from. Told apart from an empty file because an operator reads
    these and the two are different things to look at."""
    if file.pages > 0:
        return f"text prefilter: text layer is not the document, {file.pages} page(s) for the model"
    return "text prefilter: text layer is not the document and there are no pages to read"


def _no_text(file: LoadedFile) -> str:
    """Why a file yielded no text: the two reasons are told apart because one of them
    (`oversize`) is ours to raise and the other is the paper the Sejm publishes."""
    if file.oversize:
        return "text prefilter: file over the download size limit"
    return "text prefilter: no text layer and no pages to read"

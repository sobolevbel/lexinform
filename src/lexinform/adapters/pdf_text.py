"""PDF text extraction and the character budget applied before sending text to the LLM."""

import io
import logging
import re
from dataclasses import dataclass

from pypdf import PdfReader

from lexinform.sections import PAGE_BREAK

log = logging.getLogger(__name__)


class PypdfTextExtractor:
    """Extracts text from text-based PDFs (Sejm prints are not scanned)."""

    def extract(self, data: bytes) -> str:
        reader = PdfReader(io.BytesIO(data))
        pages: list[str] = []
        for index, page in enumerate(reader.pages):
            try:
                pages.append(page.extract_text() or "")
            except Exception as exc:  # pypdf raises a zoo of exceptions on odd PDFs
                log.warning("pypdf failed on page %d: %s", index + 1, exc)
        # Pages stay separated so `sections.trim_print` can recognise where appendices start.
        return _normalize_whitespace(PAGE_BREAK.join(pages))


_JUSTIFICATION_RE = re.compile(r"^\s*uzasadnienie\s*$", re.MULTILINE | re.IGNORECASE)
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_TRAILING_SPACES_RE = re.compile(r"[ \t]+\n")


def _normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\xa0", " ")
    text = _TRAILING_SPACES_RE.sub("\n", text)
    return _MULTI_BLANK_RE.sub("\n\n", text).strip()


@dataclass(frozen=True)
class BudgetedText:
    text: str
    truncated: bool
    original_chars: int


class TextBudget:
    """Cuts a long print down to `max_chars` while keeping the start of the act text and the
    start of the justification (uzasadnienie), which explains the purpose in plain language."""

    def __init__(self, max_chars: int, *, justification_share: float = 0.35) -> None:
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        self._max = max_chars
        self._justification_share = justification_share

    def apply(self, text: str) -> BudgetedText:
        total = len(text)
        if total <= self._max:
            return BudgetedText(text=text, truncated=False, original_chars=total)

        match = _JUSTIFICATION_RE.search(text)
        if match is None or match.start() < self._max:
            # No separate justification, or it already fits into the head: plain head cut.
            return BudgetedText(text=text[: self._max], truncated=True, original_chars=total)

        justification_chars = int(self._max * self._justification_share)
        head_chars = self._max - justification_chars
        head = text[:head_chars]
        justification = text[match.start() : match.start() + justification_chars]
        marker = "\n\n[... fragment pominięty ...]\n\n"
        return BudgetedText(
            text=head + marker + justification, truncated=True, original_chars=total
        )

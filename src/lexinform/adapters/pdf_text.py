"""PDF text extraction with pypdf."""

import io
import logging
import re
from collections.abc import Sequence

from pypdf import PdfReader, PdfWriter

from lexinform.sections import PAGE_BREAK

log = logging.getLogger(__name__)

_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_TRAILING_SPACES_RE = re.compile(r"[ \t]+\n")


class PypdfTextExtractor:
    """Extracts text from text-based PDFs (Sejm prints are not scanned).

    Pages are joined with `PAGE_BREAK` so `sections.trim_print` can see where appendices start.
    """

    def extract(self, data: bytes) -> str:
        pages: list[str] = []
        try:
            reader = PdfReader(io.BytesIO(data))
            for index, page in enumerate(reader.pages):
                try:
                    pages.append(page.extract_text() or "")
                except Exception as exc:  # pypdf raises a zoo of exceptions on odd PDFs
                    log.warning("pypdf failed on page %d: %s", index + 1, exc)
        except Exception as exc:
            # An empty, truncated or encrypted file: the bill falls back to its metadata, as one
            # of an unknown format does. Raising would spend its three analysis attempts instead.
            log.warning("pdf not read (%s: %s); no text", type(exc).__name__, exc)
            return ""
        return _normalize_whitespace(PAGE_BREAK.join(pages))

    def pages(self, data: bytes) -> int:
        try:
            return len(PdfReader(io.BytesIO(data)).pages)
        except Exception as exc:
            log.warning("pdf not read (%s: %s); no pages", type(exc).__name__, exc)
            return 0

    def select_pages(self, data: bytes, pages: Sequence[int]) -> bytes:
        """A new PDF of the chosen pages; the original when it cannot be taken apart."""
        try:
            reader = PdfReader(io.BytesIO(data))
            writer = PdfWriter()
            for index in pages:
                writer.add_page(reader.pages[index])
            out = io.BytesIO()
            writer.write(out)
        except Exception as exc:
            log.warning(
                "pdf pages not selected (%s: %s); sending it whole", type(exc).__name__, exc
            )
            return data
        return out.getvalue()


def _normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\xa0", " ")
    text = _TRAILING_SPACES_RE.sub("\n", text)
    return _MULTI_BLANK_RE.sub("\n\n", text).strip()

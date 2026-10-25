"""PDF text extraction with pypdf."""

import hashlib
import io
import logging

from pypdf import PdfReader, PdfWriter, __version__

from lexinform.models import PdfPageText, PdfTextExtraction

log = logging.getLogger(__name__)


class PypdfTextExtractor:
    """Physical PDF page text with the compatible whitespace-normalised string projection."""

    def extract(self, data: bytes) -> str:
        return self.extract_pages(data).legacy_text

    def extract_pages(self, data: bytes) -> PdfTextExtraction:
        pages: list[PdfPageText] = []
        error_type: str | None = None
        try:
            reader = PdfReader(io.BytesIO(data))
            for number, page in enumerate(reader.pages, 1):
                text = ""
                error: str | None = None
                try:
                    text = page.extract_text() or ""
                except Exception as exc:  # pypdf raises a zoo of exceptions on odd PDFs
                    log.warning("pypdf failed on page %d: %s", number, exc)
                    error = type(exc).__name__
                pages.append(PdfPageText(number=number, text=text, error_type=error))
        except Exception as exc:
            log.warning("pdf not read (%s: %s); no text", type(exc).__name__, exc)
            error_type = type(exc).__name__
        return PdfTextExtraction(
            input_sha256=hashlib.sha256(data).hexdigest(),
            pypdf_version=__version__,
            pages=tuple(pages),
            error_type=error_type,
        )

    def pages(self, data: bytes) -> int:
        try:
            return len(PdfReader(io.BytesIO(data)).pages)
        except Exception as exc:
            log.warning("pdf not read (%s: %s); no pages", type(exc).__name__, exc)
            return 0

    def select_pages(self, data: bytes, *, first: int, count: int) -> bytes:
        """A new PDF of the chosen pages; the original when it cannot be taken apart."""
        try:
            reader = PdfReader(io.BytesIO(data))
            writer = PdfWriter()
            for page in reader.pages[first : first + count]:
                writer.add_page(page)
            out = io.BytesIO()
            writer.write(out)
        except Exception as exc:
            log.warning(
                "pdf pages not selected (%s: %s); sending it whole", type(exc).__name__, exc
            )
            return data
        return out.getvalue()

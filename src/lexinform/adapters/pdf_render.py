"""Rendering a PDF's pages to images, for the model that maps a scan page by page.

Small and grey on purpose: the map is made from headings, and a page rendered 700 pixels wide
costs about 900 tokens on the mapping model, against the 1600 the same page costs the analysis
model at full size. Nothing here reads the pages; that is the model's job.
"""

import io
import logging

import pypdfium2 as pdfium

log = logging.getLogger(__name__)

JPEG_QUALITY = 55


class PdfPageRenderer:
    """Pages of a PDF as greyscale JPEGs, one per page, in order."""

    def render(self, data: bytes, *, max_width: int) -> list[bytes]:
        """An empty list when the file cannot be rendered: the caller then sends it whole."""
        try:
            document = pdfium.PdfDocument(data)
        except Exception as exc:
            log.warning("pdf not rendered (%s: %s); no pages", type(exc).__name__, exc)
            return []
        pages: list[bytes] = []
        try:
            for page in document:
                width = page.get_width() or max_width
                image = page.render(scale=max_width / width).to_pil().convert("L")
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
                pages.append(buffer.getvalue())
        except Exception as exc:
            log.warning(
                "pdf page %d not rendered (%s: %s)", len(pages) + 1, type(exc).__name__, exc
            )
            return []
        return pages

"""Downloading and reading bill PDFs, shared by the text prefilter and the analysis.

A run-scoped cache keeps the extracted text so a print scanned by the prefilter is not downloaded
again a few seconds later by the analysis. Nothing is persisted: the state dump lives in git.
"""

from __future__ import annotations

import logging

from lexinform.ports import SejmGateway, TextExtractor

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 200  # below this the PDF is a scan or empty: treat as "no text"


class PdfTextLoader:
    def __init__(
        self,
        gateway: SejmGateway,
        extractor: TextExtractor,
        *,
        max_bytes: int,
        cache_size: int = 32,
    ) -> None:
        self._gateway = gateway
        self._extractor = extractor
        self._max_bytes = max_bytes
        self._cache: dict[str, str | None] = {}
        self._cache_size = cache_size

    def load(self, url: str) -> str | None:
        """Extracted text of the PDF at `url`, or None when it is too big or has no text layer.

        Raises `ServiceUnavailableError` when the Sejm API is down; other problems (404, broken
        PDF) propagate as ordinary exceptions for the caller to classify.
        """
        if url in self._cache:
            return self._cache[url]
        text = self._fetch(url)
        if len(self._cache) >= self._cache_size:
            self._cache.pop(next(iter(self._cache)))
        self._cache[url] = text
        return text

    def _fetch(self, url: str) -> str | None:
        size = self._gateway.attachment_size(url)
        if size is not None and size > self._max_bytes:
            log.warning("%s is %d bytes, over limit; skipping text", url, size)
            return None
        data = self._gateway.download(url)
        if len(data) > self._max_bytes:
            log.warning("%s is %d bytes, over limit; skipping text", url, len(data))
            return None
        text = self._extractor.extract(data)
        if len(text.strip()) < MIN_TEXT_CHARS:
            log.warning("%s yielded almost no text (%d chars)", url, len(text))
            return None
        return text

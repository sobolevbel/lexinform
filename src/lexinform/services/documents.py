"""Downloading and reading the documents a bill text comes from (Sejm prints, RCL files).

A run-scoped cache keeps the extracted text so a file scanned by the text prefilter is not
downloaded again a few seconds later by the analysis. Nothing is persisted: the state dump lives
in git. Downloads are routed by host to the client of that system, so its outage semantics apply.
"""

import base64
import hashlib
import logging
import threading
import time
from collections.abc import Mapping
from urllib.parse import urlparse

from lexinform.errors import AttachmentTooLargeError, ServiceUnavailableError
from lexinform.models import ScannedDocument, TextDocument
from lexinform.ports import Downloader, TextExtractor
from lexinform.sections import PAGE_BREAK, scan_page_window

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 200
MAX_SCAN_PAGES = 600
MAX_SCAN_BYTES = 24_000_000
"""What the Messages API takes as one document: 600 pages, and 32 MB of request — which is
base64, a third larger than the file (druk 2865 is 40 MB and does not fit)."""


class TextLoader:
    """Text of remote documents, one download per URL per run.

    `downloaders` is keyed by host. A file that yields less than `MIN_TEXT_CHARS` is a scan or an
    empty document and counts as having no text at all. Loads run in parallel during the text
    prefilter, so the cache is locked.
    """

    def __init__(
        self,
        downloaders: Mapping[str, Downloader],
        extractor: TextExtractor,
        *,
        max_bytes: int,
        cache_size: int = 32,
    ) -> None:
        self._downloaders = dict(downloaders)
        self._extractor = extractor
        self._max_bytes = max_bytes
        self._cache: dict[str, str | None] = {}
        self._cache_size = cache_size
        self._lock = threading.Lock()

    def load(self, url: str) -> str | None:
        """Extracted text of the file at `url`, or None when it is too big or has no text layer.

        Raises a `ServiceUnavailableError` when the host is down; other problems (404, broken
        file, unknown host) propagate as ordinary exceptions for the caller to classify.
        """
        with self._lock:
            if url in self._cache:
                return self._cache[url]
        text = self._fetch(url)
        with self._lock:
            if len(self._cache) >= self._cache_size:
                self._cache.pop(next(iter(self._cache)))
            self._cache[url] = text
        return text

    def load_document(self, document: TextDocument) -> str | None:
        """The main file's text followed by the extra files' (pages separated by `PAGE_BREAK`);
        None when the main file yields nothing. An unreadable extra file is skipped."""
        main = self.load(document.url)
        if main is None:
            return None
        parts = [main]
        for url in document.extra_urls:
            try:
                extra = self.load(url)
            except ServiceUnavailableError:
                raise
            except Exception as exc:
                log.warning("%s unreadable (%s: %s); skipped", url, type(exc).__name__, exc)
                continue
            if extra is not None:
                parts.append(extra)
        return PAGE_BREAK.join(parts)

    def load_scan(
        self, url: str, *, cover_letter: bool, budget: int | None
    ) -> ScannedDocument | None:
        """The file itself, for a model to read as pages, cut down to the pages worth paying for
        (`sections.scan_page_window`); None when it has no pages (a Word file, an archive) or is
        too big for the API to take.

        Downloaded again rather than kept beside the text: a scan is the exception, and the
        biggest prints run to tens of megabytes. `sha256` is of the whole file, not of the pages
        sent: it says which document this is, and the trimming is ours to change.
        """
        data = self._download(url)
        if data is None:
            return None
        pages = self._extractor.pages(data)
        if pages <= 0:
            return None
        window = scan_page_window(pages, cover_letter=cover_letter, budget=budget)
        selected = data
        if (window.first, window.count) != (0, pages):
            selected = self._extractor.select_pages(data, first=window.first, count=window.count)
            log.info("%s: %d of %d pages kept for the model", url, window.count, pages)
        if len(selected) > MAX_SCAN_BYTES or window.count > MAX_SCAN_PAGES:
            log.warning(
                "%s is %d pages and %d KB: over what the model takes (%d pages, %d KB)",
                url,
                window.count,
                len(selected) // 1024,
                MAX_SCAN_PAGES,
                MAX_SCAN_BYTES // 1024,
            )
            return None
        return ScannedDocument(
            data=base64.standard_b64encode(selected).decode("ascii"),
            pages=window.count,
            of_pages=pages,
            sha256=hashlib.sha256(data).hexdigest(),
        )

    def _fetch(self, url: str) -> str | None:
        data = self._download(url)
        if data is None:
            return None
        started = time.perf_counter()
        text = self._extractor.extract(data)
        log.info("%s: %d chars extracted in %.1fs", url, len(text), time.perf_counter() - started)
        if len(text.strip()) < MIN_TEXT_CHARS:
            log.warning("%s yielded almost no text (%d chars)", url, len(text))
            return None
        return text

    def _download(self, url: str) -> bytes | None:
        host = urlparse(url).hostname or ""
        download = self._downloaders.get(host)
        if download is None:
            raise ValueError(f"no downloader for host {host!r} ({url})")
        started = time.perf_counter()
        try:
            data = download(url, max_bytes=self._max_bytes)
        except AttachmentTooLargeError as exc:
            log.warning("%s; skipping text", exc)
            return None
        log.info(
            "%s: %d KB downloaded in %.1fs",
            url,
            len(data) // 1024,
            time.perf_counter() - started,
        )
        return data

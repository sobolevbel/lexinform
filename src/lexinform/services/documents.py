"""Downloading and reading the documents a bill text comes from (Sejm prints, RCL files).

A run-scoped cache keeps what one download told us — the text, how many pages the file has,
whether it was over the size limit — so a file the text prefilter read is not downloaded again a
few seconds later by the analysis, and so that a file with no text layer can still be recognised
as a scan rather than as nothing at all. Nothing is persisted: the state dump lives in git.
Downloads are routed by host to the client of that system, so its outage semantics apply.
"""

import base64
import hashlib
import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
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


@dataclass(frozen=True)
class LoadedFile:
    """What one download told us about a file.

    `text` is None when there is none to speak of; `pages` then says whether the file is a scan
    (a PDF with pages to read) or nothing we can use at all (a Word file, an archive), and
    `oversize` whether it was simply too big to fetch. The three apart are what lets a caller
    say why a bill was skipped, and whether it should be skipped at all.
    """

    text: str | None
    pages: int = 0
    oversize: bool = False

    @property
    def is_scan(self) -> bool:
        """No text to read, but pages a model could be shown."""
        return self.text is None and self.pages > 0


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
        self._cache: dict[str, LoadedFile] = {}
        self._cache_size = cache_size
        self._lock = threading.Lock()

    def load(self, url: str) -> str | None:
        """Extracted text of the file at `url`, or None when it is too big or has no text layer.

        Raises a `ServiceUnavailableError` when the host is down; other problems (404, broken
        file, unknown host) propagate as ordinary exceptions for the caller to classify.
        """
        return self.read(url).text

    def read(self, url: str) -> LoadedFile:
        """The same download, with what the file turned out to be."""
        with self._lock:
            if url in self._cache:
                return self._cache[url]
        loaded = self._fetch(url)
        with self._lock:
            if len(self._cache) >= self._cache_size:
                self._cache.pop(next(iter(self._cache)))
            self._cache[url] = loaded
        return loaded

    def read_document(self, document: TextDocument) -> LoadedFile:
        """The main file's text followed by the extra files' (pages separated by `PAGE_BREAK`),
        with the main file's page count: the extras are its uzasadnienie and OSR, and it is the
        main file a caller falls back to reading as pages. An unreadable extra file is skipped.
        """
        main = self.read(document.url)
        if main.text is None:
            return main
        parts = [main.text]
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
        return LoadedFile(PAGE_BREAK.join(parts), pages=main.pages)

    def load_scan(self, url: str, *, cover_letter: bool) -> ScannedDocument | None:
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
        window = scan_page_window(pages, cover_letter=cover_letter)
        if window.count > MAX_SCAN_PAGES:
            log.warning(
                "%s is %d pages: more than the model takes at once (%d)", url, pages, MAX_SCAN_PAGES
            )
            return None
        selected = data
        if (window.first, window.count) != (0, pages):
            selected = self._extractor.select_pages(data, first=window.first, count=window.count)
            log.info("%s: %d of %d pages kept for the model", url, window.count, pages)
        if len(selected) > MAX_SCAN_BYTES:
            log.warning(
                "%s is %d KB: over what the model takes (%d KB)",
                url,
                len(selected) // 1024,
                MAX_SCAN_BYTES // 1024,
            )
            return None
        return ScannedDocument(
            data=base64.standard_b64encode(selected).decode("ascii"),
            pages=window.count,
            of_pages=pages,
            cover_letter_pages=window.first,
            sha256=hashlib.sha256(data).hexdigest(),
        )

    def first_pages(self, scan: ScannedDocument, count: int) -> ScannedDocument | None:
        """The opening pages of a scan already loaded, for the cheap pass to read.

        Cut from the bytes in hand rather than downloaded again: the file is the expensive thing
        about a scan and it has just been fetched. None when there is nothing to cut — the scan
        is already that short, or the extractor could not select pages — and the caller then
        shows the cheap model what it has.
        """
        if count <= 0 or scan.pages <= count:
            return None
        data = base64.standard_b64decode(scan.data)
        selected = self._extractor.select_pages(data, first=0, count=count)
        if selected is data or len(selected) >= len(data):
            return None
        return scan.model_copy(
            update={"data": base64.standard_b64encode(selected).decode("ascii"), "pages": count}
        )

    def _fetch(self, url: str) -> LoadedFile:
        data = self._download(url)
        if data is None:
            return LoadedFile(None, oversize=True)
        started = time.perf_counter()
        text = self._extractor.extract(data)
        pages = self._extractor.pages(data)
        log.info("%s: %d chars extracted in %.1fs", url, len(text), time.perf_counter() - started)
        if len(text.strip()) < MIN_TEXT_CHARS:
            log.warning("%s yielded almost no text (%d chars, %d pages)", url, len(text), pages)
            return LoadedFile(None, pages=pages)
        return LoadedFile(text, pages=pages)

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

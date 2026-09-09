"""Downloading and reading the documents a bill text comes from (Sejm prints, RCL files).

A run-scoped cache keeps the extracted text so a file scanned by the text prefilter is not
downloaded again a few seconds later by the analysis. Nothing is persisted: the state dump lives
in git. Downloads are routed by host to the client of that system, so its outage semantics apply.
"""

import logging
import threading
from collections.abc import Mapping
from urllib.parse import urlparse

from lexinform.errors import AttachmentTooLargeError, ServiceUnavailableError
from lexinform.models import TextDocument
from lexinform.ports import Downloader, TextExtractor
from lexinform.sections import PAGE_BREAK

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 200  # below this the file is a scan or empty: treat as "no text"


class TextLoader:
    """Text of remote documents, one download per URL per run."""

    def __init__(
        self,
        downloaders: Mapping[str, Downloader],
        extractor: TextExtractor,
        *,
        max_bytes: int,
        cache_size: int = 32,
    ) -> None:
        self._downloaders = dict(downloaders)  # host -> downloader
        self._extractor = extractor
        self._max_bytes = max_bytes
        self._cache: dict[str, str | None] = {}
        self._cache_size = cache_size
        self._lock = threading.Lock()  # loads run in parallel during the text prefilter

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

    def _fetch(self, url: str) -> str | None:
        host = urlparse(url).hostname or ""
        download = self._downloaders.get(host)
        if download is None:
            raise ValueError(f"no downloader for host {host!r} ({url})")
        try:
            data = download(url, max_bytes=self._max_bytes)
        except AttachmentTooLargeError as exc:
            log.warning("%s; skipping text", exc)
            return None
        text = self._extractor.extract(data)
        if len(text.strip()) < MIN_TEXT_CHARS:
            log.warning("%s yielded almost no text (%d chars)", url, len(text))
            return None
        return text

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
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from urllib.parse import urlparse

from lexinform.document_candidates import (
    AmbiguousCandidates,
    CandidateSet,
    DocumentCandidate,
    select_candidate_versions,
)
from lexinform.document_composition import (
    CandidateFallback,
    amendment_part,
    finalize_selection,
    finish_bundle,
    finish_normative,
    normative_part,
    required_part,
    start_bundle,
    supporting_failure,
    supporting_part,
)
from lexinform.document_selection import DOCUMENT_SELECTION_POLICY_VERSION
from lexinform.errors import AttachmentTooLargeError, ServiceUnavailableError
from lexinform.models import (
    ArtifactIdentity,
    ArtifactReadIdentity,
    DocumentPart,
    DocumentReadMetrics,
    DocumentSelection,
    ExtractionResult,
    HostDocumentReads,
    ScanInputManifest,
    ScannedDocument,
    TextDocument,
)
from lexinform.models.document_reads import LoadedFile as LoadedFile
from lexinform.ports import (
    Downloader,
    ProvenanceTextExtractor,
    StructuredTextExtractor,
    TextExtractor,
)
from lexinform.sections import scan_page_window

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
        self._cache: dict[str, LoadedFile] = {}
        self._cache_size = cache_size
        self._lock = threading.Lock()
        self._read_cache_hits = 0
        self._download_metrics: dict[str, Counter[str]] = {}
        self._seen_bodies: dict[str, int] = {}
        self._known_bodies: dict[str, int] = {}
        self._known_reads: dict[str, ArtifactReadIdentity] = {}
        self._extractions: dict[str, ExtractionResult] = {}

    def extractions_for_selection(
        self, selection: DocumentSelection
    ) -> tuple[ExtractionResult, ...]:
        sources = {part.source_sha256 for part in (*selection.parts, *selection.observed_parts)}
        with self._lock:
            return tuple(
                self._extractions[sha] for sha in sorted(sources) if sha in self._extractions
            )

    def read_artifacts(self) -> tuple[ArtifactIdentity, ...]:
        """Known body identities survive a counter reset without retaining body bytes."""
        with self._lock:
            return tuple(
                ArtifactIdentity(sha256=sha, byte_size=size)
                for sha, size in sorted(self._known_bodies.items())
            )

    def read_artifact_reads(self) -> tuple[ArtifactReadIdentity, ...]:
        """Return known complete-response identities without timestamps or body bytes."""
        with self._lock:
            return tuple(self._known_reads[key] for key in sorted(self._known_reads))

    def reads_for_selection(self, selection: DocumentSelection) -> tuple[ArtifactReadIdentity, ...]:
        sources = {
            (part.url, part.source_sha256) for part in (*selection.parts, *selection.observed_parts)
        }
        with self._lock:
            return tuple(
                read
                for _, read in sorted(self._known_reads.items())
                if (read.requested_url, read.sha256) in sources
            )

    def artifacts_for_selection(self, selection: DocumentSelection) -> tuple[ArtifactIdentity, ...]:
        sources = {part.source_sha256 for part in (*selection.parts, *selection.observed_parts)}
        with self._lock:
            return tuple(
                ArtifactIdentity(sha256=sha, byte_size=self._known_bodies[sha])
                for sha in sorted(sources)
                if sha in self._known_bodies
            )

    def reset_read_metrics(self) -> None:
        """The caller resets observation before starting run work without clearing cached files."""
        with self._lock:
            self._read_cache_hits = 0
            self._download_metrics.clear()
            self._seen_bodies.clear()

    def read_metrics(self) -> DocumentReadMetrics:
        with self._lock:
            return DocumentReadMetrics(
                cache_hits=self._read_cache_hits,
                distinct_bodies=len(self._seen_bodies),
                distinct_body_bytes=sum(self._seen_bodies.values()),
                hosts={
                    host: HostDocumentReads(
                        attempts=counts["attempts"],
                        completed=counts["completed"],
                        body_bytes=counts["body_bytes"],
                        over_limit=counts["over_limit"],
                        failed=counts["failed"],
                    )
                    for host, counts in sorted(self._download_metrics.items())
                },
            )

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
                self._read_cache_hits += 1
                return self._cache[url]
        loaded = self._fetch(url)
        with self._lock:
            if len(self._cache) >= self._cache_size:
                self._cache.pop(next(iter(self._cache)))
            self._cache[url] = loaded
        return loaded

    def read_document(self, document: TextDocument) -> LoadedFile:
        """Read loose-file snapshots in the existing IO order."""
        bundle = start_bundle(self.read(document.url), min_chars=MIN_TEXT_CHARS)
        if isinstance(bundle, LoadedFile):
            return bundle
        for url in document.required_urls:
            bundle = required_part(bundle, url, self.read(url))
            if isinstance(bundle, LoadedFile):
                return bundle
        for url in document.normative_urls:
            try:
                annex = self.read(url)
            except ServiceUnavailableError:
                raise
            except Exception:
                annex = None
            bundle = normative_part(bundle, url, annex)
            if isinstance(bundle, LoadedFile):
                return bundle
        bundle = finish_normative(bundle)
        for url in document.amendment_urls:
            bundle = amendment_part(bundle, url, self.read(url))
            if isinstance(bundle, LoadedFile):
                return bundle
        for url in document.extra_urls:
            try:
                loaded = self.read(url)
            except ServiceUnavailableError:
                raise
            except Exception as exc:
                log.warning("%s unreadable (%s: %s); skipped", url, type(exc).__name__, exc)
                bundle = supporting_failure(bundle, url, type(exc).__name__)
                continue
            bundle = supporting_part(bundle, url, loaded)
        return finish_bundle(bundle, document)

    def select_document(self, document: TextDocument) -> tuple[TextDocument, LoadedFile]:
        selected, loaded = self._select_document(document)
        return selected, replace(
            loaded,
            selection=loaded.selection.model_copy(
                update={"policy_version": DOCUMENT_SELECTION_POLICY_VERSION}
            ),
        )

    def _select_document(self, document: TextDocument) -> tuple[TextDocument, LoadedFile]:
        if document.compare_versions:
            resolved = self._compare_versions(document)
            if resolved is not None:
                return resolved
        fallback = CandidateFallback()
        for candidate in (document, *document.alternatives):
            if candidate.official_link_conflict:
                fallback = fallback.conflict(candidate.url)
                continue
            try:
                main = self.read(candidate.url)
            except ServiceUnavailableError:
                raise
            except Exception as exc:
                if (
                    candidate == document
                    and not document.alternatives
                    and not document.compare_versions
                ):
                    raise
                fallback = fallback.failure(
                    candidate.url, type(exc).__name__, str(exc), invalid=isinstance(exc, ValueError)
                )
                continue
            rejected = fallback.consider(
                candidate.url,
                main,
                multiple=bool(document.alternatives or document.compare_versions),
            )
            if rejected is not None:
                fallback = rejected
                continue
            return candidate, self._with_evidence(candidate, self.read_document(candidate))
        return document, fallback.result()

    def _compare_versions(self, document: TextDocument) -> tuple[TextDocument, LoadedFile] | None:
        candidates: list[tuple[TextDocument, LoadedFile]] = []
        prepared: list[DocumentCandidate] = []
        for candidate in (document, *document.alternatives):
            if candidate.official_link_conflict:
                continue
            try:
                file = self.read(candidate.url)
            except ServiceUnavailableError:
                raise
            except Exception:
                continue
            item = DocumentCandidate(
                candidate.url, file.text, file.selection.parts, file.selection.observed_parts
            )
            if item.is_bill:
                candidates.append((candidate, file))
                prepared.append(item)
        choice = select_candidate_versions(CandidateSet(tuple(prepared)))
        if choice is None:
            return None
        if isinstance(choice, AmbiguousCandidates):
            return document, LoadedFile(None, selection=choice.selection)
        candidate, _ = candidates[choice.index]
        loaded = self._with_evidence(candidate, self.read_document(candidate))
        return candidate, LoadedFile(
            loaded.text,
            pages=loaded.pages,
            oversize=loaded.oversize,
            sha256=loaded.sha256,
            selection=loaded.selection.model_copy(update={"observed_parts": choice.observed_parts}),
        )

    def _with_evidence(self, document: TextDocument, file: LoadedFile) -> LoadedFile:
        return finalize_selection(document, file)

    def load_scan(self, url: str, *, cover_letter: bool) -> ScannedDocument | None:
        """Return bounded PDF bytes with separate original and exact-input identities."""
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
            if selected == data:
                window = scan_page_window(pages, cover_letter=False)
                if window.count > MAX_SCAN_PAGES:
                    return None
            log.info("%s: %d of %d pages kept for the model", url, window.count, pages)
        if len(selected) > MAX_SCAN_BYTES:
            log.warning(
                "%s is %d KB: over what the model takes (%d KB)",
                url,
                len(selected) // 1024,
                MAX_SCAN_BYTES // 1024,
            )
            return None
        source_sha = hashlib.sha256(data).hexdigest()
        return ScannedDocument(
            data=base64.standard_b64encode(selected).decode("ascii"),
            pages=window.count,
            of_pages=pages,
            cover_letter_pages=window.first,
            sha256=source_sha,
            scan_input=ScanInputManifest(
                source_sha256=source_sha,
                source_bytes=len(data),
                source_pages=pages,
                input_sha256=hashlib.sha256(selected).hexdigest(),
                input_bytes=len(selected),
                first_page=window.first + 1,
                page_count=window.count,
                transformations=("cover_letter",) if window.first else (),
            ),
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
            update={
                "data": base64.standard_b64encode(selected).decode("ascii"),
                "pages": count,
                "scan_input": scan.scan_input.opening(selected, count) if scan.scan_input else None,
            }
        )

    def _fetch(self, url: str) -> LoadedFile:
        data = self._download(url)
        if data is None:
            return LoadedFile(None, oversize=True)
        started = time.perf_counter()
        if isinstance(self._extractor, ProvenanceTextExtractor):
            observed = self._extractor.extract_document_with_provenance(data)
            text = observed.document.text
            selection = observed.document.selection
            with self._lock:
                self._extractions[observed.result.artifact_sha256] = observed.result
        elif isinstance(self._extractor, StructuredTextExtractor):
            extracted = self._extractor.extract_document(data)
            text = extracted.text
            selection = extracted.selection
        else:
            text = self._extractor.extract(data)
            selection = DocumentSelection(
                parts=(
                    DocumentPart(
                        sha256=hashlib.sha256(data).hexdigest(),
                        text=text,
                        text_end=len(text),
                        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    ),
                )
            )
        selection = selection.model_copy(
            update={
                "parts": tuple(part.model_copy(update={"url": url}) for part in selection.parts),
                "observed_parts": tuple(
                    part.model_copy(update={"url": url}) for part in selection.observed_parts
                ),
            }
        )
        digest = hashlib.sha256(data).hexdigest()
        pages = self._extractor.pages(data)
        log.info("%s: %d chars extracted in %.1fs", url, len(text), time.perf_counter() - started)
        if len(text.strip()) < MIN_TEXT_CHARS:
            log.warning("%s yielded almost no text (%d chars, %d pages)", url, len(text), pages)
            return LoadedFile(None, pages=pages, sha256=digest, selection=selection)
        return LoadedFile(text, pages=pages, sha256=digest, selection=selection)

    def _download(self, url: str) -> bytes | None:
        host = urlparse(url).hostname or ""
        download = self._downloaders.get(host)
        if download is None:
            raise ValueError(f"no downloader for host {host!r} ({url})")
        started = time.perf_counter()
        with self._lock:
            self._download_metrics.setdefault(host, Counter())["attempts"] += 1
        try:
            data = download(url, max_bytes=self._max_bytes)
        except AttachmentTooLargeError as exc:
            with self._lock:
                self._download_metrics[host]["over_limit"] += 1
            log.warning("%s; skipping text", exc)
            return None
        except Exception:
            with self._lock:
                self._download_metrics[host]["failed"] += 1
            raise
        digest = hashlib.sha256(data).hexdigest()
        read = ArtifactReadIdentity(sha256=digest, byte_size=len(data), requested_url=url)
        with self._lock:
            self._known_reads.setdefault(read.read_id, read)
            self._download_metrics[host]["completed"] += 1
            self._download_metrics[host]["body_bytes"] += len(data)
            self._seen_bodies.setdefault(digest, len(data))
            self._known_bodies.setdefault(digest, len(data))
        log.info(
            "%s: %d KB downloaded in %.1fs",
            url,
            len(data) // 1024,
            time.perf_counter() - started,
        )
        return data

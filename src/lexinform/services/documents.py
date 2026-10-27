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

from lexinform.document_candidates import (
    AmbiguousCandidates,
    CandidateSet,
    DocumentCandidate,
    select_candidate_versions,
)
from lexinform.document_selection import bill_selection, deduplicate_bundle
from lexinform.document_versions import (
    join_normative_parts,
    normative_annex_key,
    regulation_support,
)
from lexinform.errors import AttachmentTooLargeError, ServiceUnavailableError
from lexinform.models import (
    DocumentPart,
    DocumentSelection,
    ScanInputManifest,
    ScannedDocument,
    TextDocument,
)
from lexinform.ports import Downloader, StructuredTextExtractor, TextExtractor
from lexinform.sections import (
    APPENDIX_KINDS,
    PAGE_BREAK,
    carries_the_document,
    document_kind,
    scan_page_window,
)

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
    sha256: str | None = None
    selection: DocumentSelection = DocumentSelection()

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
        if main.text is None or document_kind(main.text) in APPENDIX_KINDS:
            return main
        if main.pages and not carries_the_document(
            main.text, min_chars=MIN_TEXT_CHARS, pages=main.pages
        ):
            return main
        parts = [main.text]
        required_parts: list[DocumentPart] = []
        for url in document.required_urls:
            continuation = self.read(url)
            if continuation.text is None:
                return LoadedFile(
                    None,
                    selection=DocumentSelection(
                        state="waiting", rejected=(f"{url}: required part unavailable",)
                    ),
                )
            if document_kind(continuation.text) in APPENDIX_KINDS - {"annex"}:
                continue
            parts.append(continuation.text)
            required_parts.extend(
                part.model_copy(update={"role": "normative_continuation"})
                for part in continuation.selection.parts
            )
        evidence = [*main.selection.parts, *required_parts]
        missing = list(main.selection.rejected)
        normative: list[str] = []
        annex_parts: list[DocumentPart] = []
        for url in document.normative_urls:
            try:
                annex = self.read(url)
            except ServiceUnavailableError:
                raise
            except Exception:
                return LoadedFile(
                    None,
                    selection=DocumentSelection(
                        state="waiting", rejected=(f"{url}: normative annex unavailable",)
                    ),
                )
            if annex.text is None:
                return LoadedFile(
                    None,
                    selection=DocumentSelection(
                        state="waiting", rejected=(f"{url}: normative annex unreadable",)
                    ),
                )
            if document_kind(annex.text) in APPENDIX_KINDS - {"annex"}:
                return LoadedFile(
                    None,
                    selection=DocumentSelection(
                        state="ambiguous", rejected=(f"{url}: normative annex role not confirmed",)
                    ),
                )
            normative.append(annex.text)
            annex_parts.extend(
                part.model_copy(update={"role": "normative_annex"})
                for part in annex.selection.parts
            )
        evidence.extend(sorted(annex_parts, key=lambda p: normative_annex_key(p.text)))
        parts[0] = join_normative_parts(parts[0], sorted(normative, key=normative_annex_key))
        for url in document.amendment_urls:
            amendment = self.read(url)
            if amendment.text is None:
                return LoadedFile(
                    None,
                    selection=DocumentSelection(
                        state="waiting", rejected=(f"{url}: self-amendment unavailable",)
                    ),
                )
            evidence.extend(
                part.model_copy(update={"role": "self_amendment"})
                for part in amendment.selection.parts
            )
        for url in document.extra_urls:
            try:
                loaded = self.read(url)
                extra = loaded.text
            except ServiceUnavailableError:
                raise
            except Exception as exc:
                log.warning("%s unreadable (%s: %s); skipped", url, type(exc).__name__, exc)
                missing.append(f"{url}: {type(exc).__name__}")
                continue
            if extra is not None and regulation_support(extra):
                missing.append(f"{url}: supporting draft regulation")
            elif extra is not None:
                parts.append(extra)
                evidence.extend(loaded.selection.parts)
            else:
                missing.append(f"{url}: no extracted text")
        return LoadedFile(
            PAGE_BREAK.join(parts),
            pages=main.pages,
            sha256=main.sha256,
            selection=main.selection.model_copy(
                update={
                    "parts": tuple(evidence),
                    "rejected": tuple(missing),
                    "version": document.version_basis,
                }
            ),
        )

    def select_document(self, document: TextDocument) -> tuple[TextDocument, LoadedFile]:
        if document.compare_versions:
            resolved = self._compare_versions(document)
            if resolved is not None:
                return resolved
        rejected: list[str] = []
        unavailable = False
        oversize = False
        ambiguous = False
        for candidate in (document, *document.alternatives):
            if candidate.official_link_conflict:
                rejected.append(f"{candidate.url}: official source identity conflict")
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
                unavailable = unavailable or not isinstance(exc, ValueError)
                rejected.append(f"{candidate.url}: {type(exc).__name__}: {exc}")
                continue
            if main.text and document_kind(main.text) in APPENDIX_KINDS:
                rejected.append(f"{candidate.url}: {document_kind(main.text)}")
                continue
            if (
                main.text is None
                and not main.is_scan
                and (document.alternatives or document.compare_versions)
            ):
                oversize = oversize or main.oversize
                ambiguous = ambiguous or main.selection.state == "ambiguous"
                rejected.extend(main.selection.rejected)
                reason = "download size limit" if main.oversize else "no readable body"
                rejected.append(f"{candidate.url}: {reason}")
                continue
            return candidate, self._with_evidence(candidate, self.read_document(candidate))
        return document, LoadedFile(
            None,
            oversize=oversize,
            selection=DocumentSelection(
                state="waiting" if unavailable else "ambiguous" if ambiguous else "unsupported",
                rejected=tuple(rejected),
            ),
        )

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
        if file.text is None or file.selection.state in ("waiting", "unsupported", "ambiguous"):
            return file
        text, parts = deduplicate_bundle(file.text or "", file.selection.parts)
        file = LoadedFile(
            text,
            pages=file.pages,
            oversize=file.oversize,
            sha256=file.sha256,
            selection=file.selection.model_copy(update={"parts": parts}),
        )
        selection = bill_selection(file.selection, source_basis=document.version_basis)
        return LoadedFile(
            file.text,
            pages=file.pages,
            oversize=file.oversize,
            sha256=file.sha256,
            selection=selection,
        )

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
        if isinstance(self._extractor, StructuredTextExtractor):
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

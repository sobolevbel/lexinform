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
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, suppress
from dataclasses import replace
from typing import Literal
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
from lexinform.document_downloads import DocumentDownloadRecorder, download_failure
from lexinform.document_selection import DOCUMENT_SELECTION_POLICY_VERSION
from lexinform.errors import AttachmentTooLargeError, ServiceUnavailableError
from lexinform.metadata_omissions import MetadataUrlOmissionCollector
from lexinform.metadata_urls import MetadataUrlDecision, metadata_url_decision
from lexinform.models import (
    ArtifactIdentity,
    ArtifactReadIdentity,
    CandidateDisposition,
    CandidateDispositionTrace,
    DocumentDownloadDiagnostics,
    DocumentPart,
    DocumentReadMetrics,
    DocumentSelection,
    DocumentSelectionRecord,
    ExtractionResult,
    HostDocumentReads,
    MetadataUrlOmissions,
    ScanInputManifest,
    ScannedDocument,
    TextDocument,
)
from lexinform.models.candidate_trace import CandidateTraceReason, CandidateTraceResult
from lexinform.models.document_reads import LoadedFile as LoadedFile
from lexinform.models.source_omissions import SourceOmissionOwner
from lexinform.ports import (
    Downloader,
    ProvenanceTextExtractor,
    StructuredTextExtractor,
    TextExtractor,
)
from lexinform.sections import scan_page_window
from lexinform.services.document_history import (
    DocumentSourceRead,
    PhysicalSourceRead,
    SourceReadCapture,
)
from lexinform.services.source_omissions import (
    SourceOmissionCollector,
    SourceOmissionInput,
    SourceOmissionObservation,
    SourceOmissionScope,
)
from lexinform.source_graph_admission import source_graph_admission, source_graph_url_decisions

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
        self._download_diagnostics = DocumentDownloadRecorder()
        self._metadata_url_omissions = MetadataUrlOmissionCollector()
        self._source_omissions = SourceOmissionCollector()

    def source_omission_scope(
        self, owner: SourceOmissionOwner, document: TextDocument
    ) -> AbstractContextManager[SourceOmissionScope | None]:
        try:
            view = self._omission_view(document, None, None)
            decisions = source_graph_url_decisions(document, None, None, opaque_failure_prose=False)
            return self._source_omissions.scope(
                owner,
                role="generic",
                view=view,
                decisions=decisions,
            )
        except BaseException as exc:
            with suppress(BaseException):
                log.warning("source omission view unavailable (%s)", type(exc).__name__[:128])
            return self._source_omissions.suspended()

    def unobserved_source_scope(self) -> AbstractContextManager[None]:
        return self._source_omissions.suspended()

    def metadata_omission_scope(
        self,
        owner: SourceOmissionOwner,
        *,
        role: Literal["rcl", "senate"],
        view: object,
        urls: tuple[str, ...],
    ) -> AbstractContextManager[SourceOmissionScope | None]:
        try:
            return self._source_omissions.scope(
                owner, role=role, view=view, decisions=(metadata_url_decision(url) for url in urls)
            )
        except BaseException as exc:
            with suppress(BaseException):
                log.warning("source omission scope unavailable (%s)", type(exc).__name__[:128])
            return self._source_omissions.suspended()

    def source_omission_inputs(
        self, owners: tuple[SourceOmissionOwner, ...]
    ) -> tuple[SourceOmissionInput | None, ...]:
        try:
            return self._source_omissions.reserve(owners)
        except BaseException as exc:
            with suppress(BaseException):
                log.warning("source omission inputs unavailable (%s)", type(exc).__name__[:128])
            return tuple(None for _ in owners)

    def run_source_omission_input[T, R](
        self, slot: SourceOmissionInput | None, step: Callable[[T], R], item: T
    ) -> R:
        with self._source_omissions.observed_input(slot):
            return step(item)

    def read_source_omissions(self) -> tuple[SourceOmissionObservation, ...]:
        return self._source_omissions.snapshot()

    @staticmethod
    def _omission_view(
        requested: TextDocument, resolved: TextDocument | None, file: LoadedFile | None
    ) -> dict[str, object]:
        return {
            "requested": requested.model_dump(mode="json"),
            "resolved": resolved.model_dump(mode="json") if resolved is not None else None,
            "selection": file.selection.without_text().model_dump(mode="json") if file else None,
            "opaque_failure_prose": file.opaque_failure_prose if file else False,
        }

    def _observe_omission_file(self, url: str, file: LoadedFile) -> None:
        scope = self._source_omissions.current()
        if scope is not None:
            with scope.capture():
                artifact, read, extraction = self.file_origins(url, file)
                if artifact is not None:
                    scope.artifact(artifact)
                if read is not None:
                    scope.read(read)
                if extraction is not None:
                    scope.extraction(extraction)

    def reset_download_diagnostics(self) -> None:
        with self._lock:
            self._download_diagnostics.reset()
            self._metadata_url_omissions.reset()
            self._source_omissions.reset()

    def read_download_diagnostics(self) -> DocumentDownloadDiagnostics | None:
        with self._lock:
            return self._download_diagnostics.snapshot()

    def read_metadata_url_omissions(self) -> MetadataUrlOmissions | None:
        return self._metadata_url_omissions.snapshot()

    def observe_metadata_url(self, url: str) -> MetadataUrlDecision:
        decision = metadata_url_decision(url)
        if decision.reason is not None:
            self._metadata_url_omissions.observe(
                decision.reason, requested_url_sha256=decision.requested_url_sha256
            )
        return decision

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

    def read_extraction_results(self) -> tuple[ExtractionResult, ...]:
        """Known URL-free results share the retained body inventory's cache-origin scope."""
        with self._lock:
            return tuple(
                self._extractions[sha]
                for sha in sorted(self._extractions)
                if sha in self._known_bodies
            )

    def file_origins(
        self, url: str, loaded: LoadedFile
    ) -> tuple[ArtifactIdentity | None, ArtifactReadIdentity | None, ExtractionResult | None]:
        """Match returned outer-body identity without inferring origins from a URL or text."""
        with self._lock:
            size = self._known_bodies.get(loaded.sha256) if loaded.sha256 is not None else None
            if loaded.sha256 is None or size is None:
                return None, None, None
            artifact = ArtifactIdentity(sha256=loaded.sha256, byte_size=size)
            read = next(
                (
                    item
                    for item in self._known_reads.values()
                    if (item.requested_url, item.sha256, item.byte_size)
                    == (url, loaded.sha256, size)
                ),
                None,
            )
            return artifact, read, self._extractions.get(loaded.sha256)

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

    def capture_source_read(
        self,
        requested: TextDocument,
        resolved: TextDocument,
        file: LoadedFile,
        *,
        record_requested_document: bool = False,
    ) -> SourceReadCapture:
        scope = self._source_omissions.current()
        if scope is not None and scope.role == "generic":
            with scope.capture():
                scope.update(
                    self._omission_view(requested, resolved, file),
                    source_graph_url_decisions(
                        requested,
                        resolved,
                        file.selection,
                        opaque_failure_prose=file.opaque_failure_prose,
                    ),
                )
        physical = PhysicalSourceRead(
            self.artifacts_for_selection(file.selection),
            self.reads_for_selection(file.selection),
            self.extractions_for_selection(file.selection),
        )
        admitted = True
        for decision in source_graph_url_decisions(
            requested,
            resolved,
            file.selection,
            opaque_failure_prose=file.opaque_failure_prose,
        ):
            if decision.reason is not None:
                admitted = False
                self._metadata_url_omissions.observe(
                    decision.reason, requested_url_sha256=decision.requested_url_sha256
                )
        if not admitted:
            artifact, read, extraction = self.file_origins(resolved.url, file)
            artifacts = {item.sha256: item for item in physical.artifacts}
            reads = {item.read_id: item for item in physical.reads}
            extractions = {item.result_id: item for item in physical.extractions}
            if artifact is not None:
                artifacts.setdefault(artifact.sha256, artifact)
            if read is not None:
                reads.setdefault(read.read_id, read)
            if extraction is not None:
                extractions.setdefault(extraction.result_id, extraction)
            observation = None
            if scope is not None and not scope.failed and scope.role == "generic":
                with scope.capture():
                    observation = scope.observation()
            if observation is not None:
                artifacts.update((item.sha256, item) for item in observation.artifacts)
                reads.update((item.read_id, item) for item in observation.reads)
                extractions.update((item.result_id, item) for item in observation.extractions)
            return PhysicalSourceRead(
                tuple(artifacts[key] for key in sorted(artifacts)),
                tuple(reads[key] for key in sorted(reads)),
                tuple(extractions[key] for key in sorted(extractions)),
                omission=observation.record if observation is not None else None,
            )
        return DocumentSourceRead(
            requested if record_requested_document else resolved,
            DocumentSelectionRecord.from_selection(file.selection),
            physical.artifacts,
            reads=physical.reads,
            extractions=physical.extractions,
            candidate_trace=file.candidate_trace,
        )

    def reset_read_metrics(self) -> None:
        """The caller resets observation before starting run work without clearing cached files."""
        with self._lock:
            self._read_cache_hits = 0
            self._download_metrics.clear()
            self._seen_bodies.clear()
            self._download_diagnostics.reset()
            self._metadata_url_omissions.reset()
            self._source_omissions.reset()

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
            cached = self._cache.get(url)
            if cached is not None:
                self._read_cache_hits += 1
                self._download_diagnostics.cache_hit()
        if cached is not None:
            self._observe_omission_file(url, cached)
            return cached
        loaded = self._fetch(url)
        with self._lock:
            if len(self._cache) >= self._cache_size:
                self._cache.pop(next(iter(self._cache)))
            self._cache[url] = loaded
        self._observe_omission_file(url, loaded)
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
        dispositions: list[CandidateDisposition] = []
        selected, loaded, result, reason = self._select_document(document, dispositions)
        try:
            trace = (
                CandidateDispositionTrace.from_decisions(
                    document,
                    tuple(dispositions),
                    result=result,
                    reason=reason,
                    selection_state=loaded.selection.state,
                )
                if source_graph_admission(
                    document,
                    selected,
                    loaded.selection,
                    opaque_failure_prose=False,
                ).admitted
                else None
            )
        except Exception:
            log.warning("candidate disposition trace omitted after metadata failure")
            trace = None
        if trace is not None and trace.is_routine_singleton:
            trace = None
        return selected, replace(
            loaded,
            selection=loaded.selection.model_copy(
                update={"policy_version": DOCUMENT_SELECTION_POLICY_VERSION}
            ),
            candidate_trace=trace,
        )

    def _select_document(
        self, document: TextDocument, dispositions: list[CandidateDisposition]
    ) -> tuple[TextDocument, LoadedFile, CandidateTraceResult, CandidateTraceReason]:
        if document.compare_versions:
            resolved = self._compare_versions(document, dispositions)
            if resolved is not None:
                return resolved
        fallback = CandidateFallback()
        candidates = (document, *document.alternatives)
        for index, candidate in enumerate(candidates):
            if candidate.official_link_conflict:
                fallback = fallback.conflict(candidate.url)
                dispositions.append(
                    CandidateDisposition(
                        phase="fallback",
                        index=index,
                        outcome="official_conflict",
                        reason="official_link_conflict",
                    )
                )
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
                dispositions.append(
                    CandidateDisposition(
                        phase="fallback",
                        index=index,
                        outcome="read_failure",
                        reason="invalid_response" if isinstance(exc, ValueError) else "read_error",
                    )
                )
                continue
            rejected = fallback.consider(
                candidate.url,
                main,
                multiple=bool(document.alternatives or document.compare_versions),
            )
            if rejected is not None:
                fallback = rejected
                dispositions.append(
                    CandidateDisposition(
                        phase="fallback",
                        index=index,
                        outcome="policy_excluded",
                        reason=(
                            "appendix_kind"
                            if main.text is not None
                            else "download_size_limit"
                            if main.oversize
                            else "no_readable_body"
                        ),
                        observed_sha256=main.sha256,
                    )
                )
                continue
            loaded = self._with_evidence(candidate, self.read_document(candidate))
            dispositions.append(
                CandidateDisposition(
                    phase="fallback",
                    index=index,
                    outcome="selected",
                    reason="first_acceptable_candidate",
                    observed_sha256=main.sha256,
                )
            )
            dispositions.extend(
                CandidateDisposition(
                    phase="fallback",
                    index=remaining,
                    outcome="unattempted",
                    reason="selection_stopped",
                )
                for remaining in range(index + 1, len(candidates))
            )
            return candidate, loaded, "selected", "first_acceptable_candidate"
        return document, fallback.result(), "fallback", "all_rejected"

    def _compare_versions(
        self, document: TextDocument, dispositions: list[CandidateDisposition]
    ) -> tuple[TextDocument, LoadedFile, CandidateTraceResult, CandidateTraceReason] | None:
        candidates: list[tuple[TextDocument, LoadedFile]] = []
        prepared: list[DocumentCandidate] = []
        candidate_indices: list[int] = []
        decisions: list[CandidateDisposition] = []
        for index, candidate in enumerate((document, *document.alternatives)):
            if candidate.official_link_conflict:
                decisions.append(
                    CandidateDisposition(
                        phase="version_comparison",
                        index=index,
                        outcome="official_conflict",
                        reason="official_link_conflict",
                    )
                )
                continue
            try:
                file = self.read(candidate.url)
            except ServiceUnavailableError:
                raise
            except Exception as exc:
                decisions.append(
                    CandidateDisposition(
                        phase="version_comparison",
                        index=index,
                        outcome="read_failure",
                        reason="invalid_response" if isinstance(exc, ValueError) else "read_error",
                    )
                )
                continue
            item = DocumentCandidate(
                candidate.url, file.text, file.selection.parts, file.selection.observed_parts
            )
            if item.is_bill:
                candidates.append((candidate, file))
                prepared.append(item)
                candidate_indices.append(index)
                decisions.append(
                    CandidateDisposition(
                        phase="version_comparison",
                        index=index,
                        outcome="policy_excluded",
                        reason="version_preference",
                        observed_sha256=file.sha256,
                    )
                )
            else:
                decisions.append(
                    CandidateDisposition(
                        phase="version_comparison",
                        index=index,
                        outcome="non_bill" if file.text is not None else "unclassified",
                        reason=(
                            "non_bill_body"
                            if file.text is not None
                            else "download_size_limit"
                            if file.oversize
                            else "ambiguous_extraction"
                            if file.selection.state == "ambiguous"
                            else "scan_without_text"
                            if file.is_scan
                            else "no_readable_body"
                        ),
                        observed_sha256=file.sha256,
                    )
                )
        choice = select_candidate_versions(CandidateSet(tuple(prepared)))
        if choice is None:
            dispositions.extend(decisions)
            return None
        if isinstance(choice, AmbiguousCandidates):
            dispositions.extend(
                item.model_copy(update={"reason": "version_conflict"})
                if item.outcome == "policy_excluded"
                else item
                for item in decisions
            )
            return (
                document,
                LoadedFile(None, selection=choice.selection),
                "ambiguous",
                "version_conflict",
            )
        candidate, _ = candidates[choice.index]
        loaded = self._with_evidence(candidate, self.read_document(candidate))
        selected_index = candidate_indices[choice.index]
        dispositions.extend(
            item.model_copy(update={"outcome": "selected", "reason": "version_choice"})
            if item.index == selected_index
            else item
            for item in decisions
        )
        return (
            candidate,
            LoadedFile(
                loaded.text,
                pages=loaded.pages,
                oversize=loaded.oversize,
                sha256=loaded.sha256,
                selection=loaded.selection.model_copy(
                    update={"observed_parts": choice.observed_parts}
                ),
                opaque_failure_prose=loaded.opaque_failure_prose,
            ),
            "selected",
            "version_choice",
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
            scope = self._source_omissions.current()
            if (
                scope is not None
                and observed.result.artifact_sha256 == hashlib.sha256(data).hexdigest()
            ):
                with scope.capture():
                    scope.extraction(observed.result)
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
        with self._lock:
            self._download_diagnostics.started()
        try:
            host = urlparse(url).hostname or ""
        except ValueError:
            with self._lock:
                self._download_diagnostics.failure(url, None, "invalid_url", "invalid_url")
            raise
        download = self._downloaders.get(host)
        if download is None:
            with self._lock:
                self._download_diagnostics.failure(url, None, "unknown_host", "unconfigured_host")
            raise ValueError(f"no downloader for host {host!r} ({url})")
        started = time.perf_counter()
        with self._lock:
            self._download_metrics.setdefault(host, Counter())["attempts"] += 1
        try:
            data = download(url, max_bytes=self._max_bytes)
        except AttachmentTooLargeError as exc:
            with self._lock:
                self._download_metrics[host]["over_limit"] += 1
                self._download_diagnostics.failure(
                    url, host, "over_limit", "byte_limit", limit_bytes=exc.limit
                )
            log.warning("%s; skipping text", exc)
            return None
        except Exception as exc:
            outcome, reason, status = download_failure(exc)
            with self._lock:
                self._download_metrics[host]["failed"] += 1
                self._download_diagnostics.failure(url, host, outcome, reason, status_code=status)
            raise
        digest = hashlib.sha256(data).hexdigest()
        with self._lock:
            self._known_bodies.setdefault(digest, len(data))
        scope = self._source_omissions.current()
        if scope is not None:
            with scope.capture():
                scope.artifact(ArtifactIdentity(sha256=digest, byte_size=len(data)))
        decision = metadata_url_decision(url)
        if decision.reason is None:
            read = ArtifactReadIdentity(sha256=digest, byte_size=len(data), requested_url=url)
            with self._lock:
                self._known_reads.setdefault(read.read_id, read)
            if scope is not None and not scope.failed:
                with scope.capture():
                    scope.read(read)
        else:
            self._metadata_url_omissions.observe(
                decision.reason,
                requested_url_sha256=decision.requested_url_sha256,
                artifact_sha256=digest,
            )
        with self._lock:
            self._download_diagnostics.completed()
            self._download_metrics[host]["completed"] += 1
            self._download_metrics[host]["body_bytes"] += len(data)
            self._seen_bodies.setdefault(digest, len(data))
        log.info(
            "%s: %d KB downloaded in %.1fs",
            url,
            len(data) // 1024,
            time.perf_counter() - started,
        )
        return data

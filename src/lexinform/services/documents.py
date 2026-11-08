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
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, suppress
from contextvars import ContextVar, Token
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Literal
from urllib.parse import urlparse

from lexinform.archive_disposition_observations import (
    ArchiveDispositionAccumulator,
    ArchiveRootObservation,
    Phase,
    complete_standalone_root,
)
from lexinform.archive_policy_capture import optional_archive_policy_capture
from lexinform.document_candidates import (
    AmbiguousCandidates,
    CandidateSet,
    DocumentCandidate,
    select_candidate_versions,
)
from lexinform.document_composition import (
    CandidateFallback,
    NormativeAnnexFailure,
    RequiredPartFailure,
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
from lexinform.document_downloads import (
    DocumentDownloadRecorder,
    download_failure,
    read_exclusion_reason,
)
from lexinform.document_selection import DOCUMENT_SELECTION_POLICY_VERSION
from lexinform.errors import AttachmentTooLargeError, ServiceUnavailableError
from lexinform.local_timing import observe_read_timing
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
from lexinform.models.candidate_trace import (
    MAX_ADMITTED_NORMATIVE_ANNEXES,
    MAX_ADMITTED_SUPPORT,
    MAX_COMPOSED_SUPPORT,
    MAX_SUPPORTING_EXCLUSIONS,
    MAX_VERSION_DECISION_ROWS,
    AdmittedNormativeAnnex,
    AdmittedNormativeAnnexes,
    AdmittedSupport,
    AdmittedSupportPart,
    AmendmentPartGap,
    ArchiveVersionConflictEvidence,
    ArchiveVersionDecisionEvidence,
    CandidateTraceReason,
    CandidateTraceResult,
    ComposedSupport,
    ComposedSupportPart,
    NormativeAnnexGap,
    RequiredPartGap,
    SupportingExclusionReason,
    SupportingPartExclusion,
    SupportingPartExclusions,
    VersionDecisionEvidence,
    composed_support_sha256,
)
from lexinform.models.document_reads import DocumentPhysicalDiagnostics
from lexinform.models.document_reads import LoadedFile as LoadedFile
from lexinform.models.extractions import DocumentExtraction
from lexinform.models.scan_input import MAX_SCAN_BYTES, MAX_SCAN_PAGES
from lexinform.models.source_omissions import SourceOmissionOwner
from lexinform.observation_order import ObservationOrder
from lexinform.physical_observations import (
    OrderedPhysicalObservation,
    PhysicalInvocation,
    PhysicalObservationCollector,
)
from lexinform.physical_walk_capture import PhysicalWalkObservation, suspend_physical_walk_capture
from lexinform.ports import (
    Downloader,
    ProvenanceTextExtractor,
    StructuredTextExtractor,
    TextExtractor,
)
from lexinform.required_part_capture import RequiredPartCapture
from lexinform.sections import Kind, scan_page_window
from lexinform.services.document_history import (
    DocumentSourceRead,
    PhysicalSourceRead,
    SourceReadCapture,
)
from lexinform.services.source_catalog_attempts import SourceCatalogObservation
from lexinform.services.source_omissions import (
    SourceOmissionCollector,
    SourceOmissionInput,
    SourceOmissionObservation,
    SourceOmissionScope,
)
from lexinform.source_graph_admission import source_graph_admission, source_graph_url_decisions
from lexinform.word_formula_placement import WordFormulaSource

log = logging.getLogger(__name__)

MAX_DEFERRED_ARCHIVE_TRACES = 256
MIN_TEXT_CHARS = 200


def _trace_with_archive_evidence(
    trace: CandidateDispositionTrace,
    evidence: ArchiveVersionConflictEvidence | ArchiveVersionDecisionEvidence,
) -> CandidateDispositionTrace:
    field = (
        "archive_version_conflict"
        if isinstance(evidence, ArchiveVersionConflictEvidence)
        else "archive_version_decision"
    )
    return CandidateDispositionTrace.model_validate({**trace.model_dump(), field: evidence})


@dataclass(frozen=True)
class _DownloadedDocument:
    data: bytes
    artifact: ArtifactIdentity


@dataclass(frozen=True)
class _CachedDocument:
    file: LoadedFile
    observation: PhysicalWalkObservation | None = None
    archive: ArchiveRootObservation | None = None


_ARCHIVE_READ_SINK: ContextVar[
    tuple[object, str, int, Callable[[ArchiveRootObservation], None]] | None
] = ContextVar("archive_read_sink", default=None)


@dataclass
class _CompositionCapture:
    required: RequiredPartCapture
    normative_gap: NormativeAnnexGap | None = None
    admitted_annexes: dict[tuple[Phase, int, int], AdmittedNormativeAnnex] = field(
        default_factory=dict
    )
    required_gap: RequiredPartGap | None = None
    amendment_gap: AmendmentPartGap | None = None
    supporting: list[SupportingPartExclusion] = field(default_factory=list)
    supporting_omitted: int = 0
    admitted_support: dict[tuple[Phase, int, int], tuple[str, str]] = field(default_factory=dict)
    composed_support: dict[tuple[Phase, int, int], tuple[DocumentPart, ...]] = field(
        default_factory=dict
    )
    version_decision: VersionDecisionEvidence | None = None
    version_decision_includes_archive: bool = False

    def exclude_support(
        self,
        phase: Phase,
        index: int,
        position: int,
        reason: SupportingExclusionReason,
        sha256: str | None = None,
    ) -> None:
        if len(self.supporting) == MAX_SUPPORTING_EXCLUSIONS:
            self.supporting_omitted += 1
            return
        self.supporting.append(
            SupportingPartExclusion(
                phase=phase, index=index, extra_index=position, reason=reason, file_sha256=sha256
            )
        )


def _selected_admitted_support(
    trace: CandidateDispositionTrace,
    selected: TextDocument,
    loaded: LoadedFile,
    capture: _CompositionCapture,
) -> AdmittedSupport | None:
    parent = next((row for row in trace.dispositions if row.outcome == "selected"), None)
    if parent is None:
        return None
    rows: list[AdmittedSupportPart] = []
    omitted = 0
    occurrences = Counter(selected.extra_urls)
    for position, url in enumerate(selected.extra_urls):
        hashes = capture.admitted_support.get((parent.phase, parent.index, position))
        if hashes is None or occurrences[url] != 1:
            continue
        parts = [part for part in loaded.selection.parts if part.url == url]
        if len(parts) != 1:
            continue
        part = parts[0]
        if (
            part.role not in ("justification", "osr")
            or part.members
            or (part.sha256, part.text_sha256) != hashes
        ):
            continue
        if len(rows) == MAX_ADMITTED_SUPPORT:
            omitted += 1
            continue
        rows.append(
            AdmittedSupportPart(
                extra_index=position,
                role=part.role,
                file_sha256=part.sha256,
                text_sha256=part.text_sha256,
            )
        )
    if not rows:
        return None
    return AdmittedSupport(
        phase=parent.phase, index=parent.index, rows=tuple(rows), omitted=omitted
    )


def _selected_composed_support(
    trace: CandidateDispositionTrace,
    selected: TextDocument,
    loaded: LoadedFile,
    capture: _CompositionCapture,
) -> tuple[ComposedSupportPart, ...]:
    parent = next((row for row in trace.dispositions if row.outcome == "selected"), None)
    if parent is None:
        return ()
    admitted = trace.admitted_support.rows if trace.admitted_support is not None else ()
    excluded = trace.supporting_exclusions.rows if trace.supporting_exclusions is not None else ()
    known = {row.extra_index for row in admitted} | {row.extra_index for row in excluded}
    other_urls = {
        selected.url,
        *selected.required_urls,
        *selected.amendment_urls,
        *selected.normative_urls,
    }
    occurrences = Counter(selected.extra_urls)
    rows: list[ComposedSupportPart] = []
    for position, url in enumerate(selected.extra_urls):
        appended = capture.composed_support.get((parent.phase, parent.index, position))
        if (
            not appended
            or position in known
            or occurrences[url] != 1
            or url in other_urls
            or not metadata_url_decision(url).admitted
        ):
            continue
        indices = [index for index, part in enumerate(loaded.selection.parts) if part.url == url]
        if len(indices) != len(appended):
            continue
        start = indices[0]
        end = start + len(appended)
        if indices != list(range(start, end)):
            continue
        selected_parts = loaded.selection.parts[start:end]
        if any(
            before.model_copy(update={"role": after.role}) != after
            for before, after in zip(appended, selected_parts, strict=True)
        ):
            continue
        rows.append(
            ComposedSupportPart(
                extra_index=position,
                first_part=start,
                part_count=len(selected_parts),
                parts_sha256=composed_support_sha256(selected_parts),
            )
        )
        if len(rows) == MAX_COMPOSED_SUPPORT:
            break
    return tuple(rows)


def _with_duplicate_support(
    trace: CandidateDispositionTrace,
    selected: TextDocument,
    loaded: LoadedFile,
    capture: _CompositionCapture,
    archive_evidence: ArchiveVersionDecisionEvidence | ArchiveVersionConflictEvidence | None,
) -> CandidateDispositionTrace:
    parent = next((row for row in trace.dispositions if row.outcome == "selected"), None)
    if parent is None:
        return trace
    existing = trace.supporting_exclusions
    rows = existing.rows if existing is not None else ()
    if len(rows) == MAX_SUPPORTING_EXCLUSIONS:
        return trace
    earlier_urls = {
        selected.url,
        *selected.required_urls,
        *selected.amendment_urls,
        *selected.normative_urls,
    }
    occurrences = Counter(selected.extra_urls)
    for position, url in enumerate(selected.extra_urls):
        earlier = earlier_urls.copy()
        earlier_urls.add(url)
        appended = capture.composed_support.get((parent.phase, parent.index, position), ())
        if (
            len(appended) != 1
            or occurrences[url] != 1
            or url in earlier
            or not metadata_url_decision(url).admitted
            or any(part.url == url for part in loaded.selection.parts)
        ):
            continue
        (source,) = appended
        if source.members or not source.sha256 or not source.text_sha256:
            continue
        matches = [
            index
            for index, part in enumerate(loaded.selection.parts)
            if part.url in earlier
            and part.model_copy(update={"url": url, "role": source.role}) == source
        ]
        if len(matches) != 1:
            continue
        row = SupportingPartExclusion(
            phase=parent.phase,
            index=parent.index,
            extra_index=position,
            reason="duplicate_standalone",
            file_sha256=source.sha256,
            duplicate_of_part=matches[0],
            duplicate_text_sha256=source.text_sha256,
        )
        offered = tuple(sorted((*rows, row), key=lambda item: item.extra_index))
        try:
            candidate = CandidateDispositionTrace.model_validate(
                {
                    **trace.model_dump(),
                    "supporting_exclusions": SupportingPartExclusions(rows=offered),
                }
            )
            if archive_evidence is not None:
                _trace_with_archive_evidence(candidate, archive_evidence)
        except ValueError:
            break
        trace = candidate
        rows = offered
        if len(rows) == MAX_SUPPORTING_EXCLUSIONS:
            break
    return trace


def _selected_admitted_annexes(
    trace: CandidateDispositionTrace,
    selected: TextDocument,
    loaded: LoadedFile,
    capture: _CompositionCapture,
) -> AdmittedNormativeAnnexes | None:
    parent = next((row for row in trace.dispositions if row.outcome == "selected"), None)
    if parent is None:
        return None
    rows: list[AdmittedNormativeAnnex] = []
    occurrences = Counter(selected.normative_urls)
    for position, url in enumerate(selected.normative_urls):
        row = capture.admitted_annexes.get((parent.phase, parent.index, position))
        if row is None or occurrences[url] != 1:
            continue
        parts = [part for part in loaded.selection.parts if part.url == url]
        if len(parts) == 1:
            part = parts[0]
            if (
                part.role == "normative_annex"
                and not part.members
                and not part.losses
                and part.text_start == 0
                and part.text_end is not None
                and part.text_end > 0
                and (part.sha256, part.text_sha256) == (row.file_sha256, row.text_sha256)
            ):
                rows.append(row)
    if not rows:
        return None
    return AdmittedNormativeAnnexes(
        phase=parent.phase,
        index=parent.index,
        rows=tuple(rows[:MAX_ADMITTED_NORMATIVE_ANNEXES]),
        omitted=max(0, len(rows) - MAX_ADMITTED_NORMATIVE_ANNEXES),
    )


@dataclass(frozen=True)
class _CompositionScope:
    owner: object
    thread_id: int
    capture: _CompositionCapture
    phase: Phase | None = None
    index: int | None = None
    document: TextDocument | None = None


_COMPOSITION_SCOPE: ContextVar[_CompositionScope | None] = ContextVar(
    "composition_scope", default=None
)


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
        capture_word_formulas: Callable[[bytes, DocumentExtraction], WordFormulaSource | None]
        | None = None,
        physical_factory: Callable[[ObservationOrder], PhysicalObservationCollector] | None = None,
    ) -> None:
        self._downloaders = dict(downloaders)
        self._extractor = extractor
        self._max_bytes = max_bytes
        self._cache: dict[str, _CachedDocument] = {}
        self._cache_size = cache_size
        self._capture_word_formulas = capture_word_formulas
        self._lock = threading.Lock()
        self._archive_traces: dict[tuple[str, str, str], CandidateDispositionTrace] = {}
        self._read_cache_hits = 0
        self._download_metrics: dict[str, Counter[str]] = {}
        self._seen_bodies: dict[str, int] = {}
        self._known_bodies: dict[str, int] = {}
        self._known_reads: dict[str, ArtifactReadIdentity] = {}
        self._extractions: dict[str, ExtractionResult] = {}
        self._download_diagnostics = DocumentDownloadRecorder()
        self._metadata_url_omissions = MetadataUrlOmissionCollector()
        self._observation_order = ObservationOrder()
        self._source_omissions = SourceOmissionCollector(self._observation_order)
        self._physical: PhysicalObservationCollector | None = None
        self._physical_setup_failed = False
        self._physical_reset_failed = False
        if physical_factory is not None:
            try:
                self._physical = physical_factory(self._observation_order)
            except BaseException:
                self._physical_setup_failed = True

    def source_omission_scope(
        self, owner: SourceOmissionOwner, document: TextDocument
    ) -> AbstractContextManager[SourceOmissionScope | None]:
        try:
            view = self._omission_view(document, None, None)
            decisions = source_graph_url_decisions(document, None, None, opaque_failure_prose=False)
            return self._source_omissions.scope(
                owner,
                role="generic",
                catalog_document=document,
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
            try:
                return self._source_omissions.unavailable_inputs(owners)
            except BaseException:
                return tuple(None for _ in owners)

    def run_source_omission_input[T, R](
        self, slot: SourceOmissionInput | None, step: Callable[[T], R], item: T
    ) -> R:
        with self._source_omissions.observed_input(slot):
            return step(item)

    def read_source_catalog_attempts(self) -> tuple[SourceCatalogObservation, ...]:
        return self._source_omissions.catalog_snapshot()

    def _observe_source_failure(self, error: BaseException) -> None:
        scope = None
        try:
            scope = self._source_omissions.current()
            if scope is not None:
                with scope.capture():
                    scope.read_failed(error)
        except BaseException as metadata_error:
            if scope is not None:
                scope.failed = True
            with suppress(BaseException):
                log.warning(
                    "source failure capture unavailable (%s)", type(metadata_error).__name__[:128]
                )

    def _physical_note_failure(self, reason: str) -> None:
        try:
            if self._physical is not None:
                self._physical.note_failure(reason)
                return
        except BaseException:
            pass
        self._physical_setup_failed = True

    def read_physical_observations(self) -> tuple[OrderedPhysicalObservation, ...]:
        if self._physical is None or self._physical_reset_failed:
            return ()
        try:
            return self._physical.snapshot()
        except BaseException:
            self._physical_note_failure("snapshot_unavailable")
            return ()

    def read_physical_diagnostics(self) -> DocumentPhysicalDiagnostics | None:
        diagnostics = None
        if self._physical is not None and not self._physical_reset_failed:
            try:
                diagnostics = self._physical.diagnostics()
            except BaseException:
                self._physical_setup_failed = True
        if self._physical_setup_failed or self._physical_reset_failed:
            if diagnostics is not None:
                reasons = dict(diagnostics.reasons)
                if len(reasons) < 8:
                    reasons["observer_callback_unavailable"] = 1
                return diagnostics.model_copy(
                    update={
                        "unknown_remainders": True,
                        "observer_failures": diagnostics.observer_failures + 1,
                        "reasons": reasons,
                    }
                )
            return DocumentPhysicalDiagnostics(
                observer_failures=1,
                unknown_remainders=True,
                reasons={"observer_setup_unavailable": 1},
            )
        return diagnostics

    def reset_physical_observations(self) -> None:
        if self._physical is not None:
            try:
                self._physical.reset()
                self._physical_reset_failed = False
                self._physical_setup_failed = False
            except BaseException:
                self._physical_reset_failed = True
                self._physical_note_failure("reset_unavailable")

    @contextmanager
    def _physical_invocation(
        self, artifact: ArtifactIdentity
    ) -> Iterator[PhysicalInvocation | None]:
        if self._physical is None or self._physical_reset_failed:
            if self._physical_setup_failed or self._physical_reset_failed:
                with suspend_physical_walk_capture():
                    yield None
            else:
                yield None
            return
        manager = None
        invocation = None
        try:
            candidate = self._physical.invocation(artifact)
            invocation = candidate.__enter__()
            manager = candidate
        except BaseException:
            self._physical_note_failure("invocation_setup_unavailable")
        body_error: BaseException | None = None
        try:
            if manager is None:
                with suspend_physical_walk_capture():
                    yield None
            else:
                yield invocation
        except BaseException as error:
            body_error = error
            raise
        finally:
            if manager is not None:
                try:
                    manager.__exit__(
                        type(body_error) if body_error is not None else None,
                        body_error,
                        body_error.__traceback__ if body_error is not None else None,
                    )
                except BaseException:
                    self._physical_note_failure("invocation_cleanup_unavailable")

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
            self.reset_physical_observations()

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
                scope.selection_observed()
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
        trace = file.candidate_trace
        if (
            record_requested_document
            and requested != resolved
            and trace is not None
            and (
                trace.admitted_support is not None
                or trace.admitted_normative_annexes is not None
                or trace.composed_support is not None
            )
        ):
            trace = trace.model_copy(
                update={
                    "admitted_support": None,
                    "admitted_normative_annexes": None,
                    "composed_support": None,
                }
            )
        if record_requested_document and requested != resolved and trace is not None:
            exclusions = trace.supporting_exclusions
            if exclusions is not None:
                remaining = tuple(
                    row for row in exclusions.rows if row.reason != "duplicate_standalone"
                )
                if remaining != exclusions.rows:
                    trace = trace.model_copy(
                        update={
                            "supporting_exclusions": SupportingPartExclusions(rows=remaining)
                            if remaining
                            else None
                        }
                    )
        defer_archive_trace = None
        evidence = file.archive_version_evidence
        if evidence is not None and trace is not None:
            with suppress(Exception):
                enriched = _trace_with_archive_evidence(trace, evidence)
                defer_archive_trace = partial(self._defer_archive_trace, trace.trace_id, enriched)
        return DocumentSourceRead(
            requested if record_requested_document else resolved,
            DocumentSelectionRecord.from_selection(file.selection),
            physical.artifacts,
            reads=physical.reads,
            extractions=physical.extractions,
            candidate_trace=trace,
            defer_archive_trace=defer_archive_trace,
        )

    def _defer_archive_trace(
        self, base_id: str, enriched: CandidateDispositionTrace, observation_id: str
    ) -> None:
        with self._lock:
            if len(self._archive_traces) < MAX_DEFERRED_ARCHIVE_TRACES:
                self._archive_traces.setdefault(
                    (observation_id, base_id, enriched.trace_id), enriched
                )

    def deferred_archive_traces(self) -> tuple[tuple[str, str, CandidateDispositionTrace], ...]:
        with self._lock:
            return tuple(
                (observation_id, base_id, trace)
                for (observation_id, base_id, _), trace in self._archive_traces.items()
            )

    def reset_read_metrics(self) -> None:
        """The caller resets observation before starting run work without clearing cached files."""
        with self._lock:
            self._read_cache_hits = 0
            self._archive_traces.clear()
            self._download_metrics.clear()
            self._seen_bodies.clear()
            self._download_diagnostics.reset()
            self._metadata_url_omissions.reset()
            self._source_omissions.reset()
            self.reset_physical_observations()

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
        sink: Callable[[ArchiveRootObservation], None] | None = None
        with suppress(Exception):
            scope = _ARCHIVE_READ_SINK.get()
            if (
                scope is not None
                and scope[0] is self
                and scope[1] == url
                and scope[2] == threading.get_ident()
            ):
                sink = scope[3]
        return self._read_entry(url, sink).file

    def read_bounded(self, url: str, *, max_bytes: int) -> LoadedFile:
        return self._read_entry(url, max_bytes=max_bytes).file

    def _read_entry(
        self,
        url: str,
        archive_sink: Callable[[ArchiveRootObservation], None] | None = None,
        *,
        max_bytes: int | None = None,
    ) -> _CachedDocument:
        with self._lock:
            cached = self._cache.get(url)
            if (
                cached is not None
                and max_bytes is not None
                and cached.file.sha256 is not None
                and self._known_bodies[cached.file.sha256] > max_bytes
            ):
                return _CachedDocument(LoadedFile(None, oversize=True))
            if cached is not None:
                self._read_cache_hits += 1
                self._download_diagnostics.cache_hit()
        if cached is not None:
            if (
                self._physical is not None
                and not self._physical_reset_failed
                and cached.file.sha256 is not None
            ):
                try:
                    self._physical.cached(cached.observation)
                except BaseException:
                    self._physical_note_failure("cache_callback_unavailable")
            self._observe_omission_file(url, cached.file)
            if archive_sink is not None and cached.archive is not None:
                with suppress(Exception):
                    archive_sink(cached.archive)
            observe_read_timing(self, "cache_reuse")
            return cached
        try:
            fetched = self._fetch(url, archive_sink, max_bytes=max_bytes)
        except BaseException as error:
            self._observe_source_failure(error)
            raise
        loaded = fetched.file
        if max_bytes is not None and loaded.oversize:
            return fetched
        with self._lock:
            if len(self._cache) >= self._cache_size:
                self._cache.pop(next(iter(self._cache)))
            self._cache[url] = fetched
        self._observe_omission_file(url, loaded)
        observe_read_timing(self, "fresh")
        return fetched

    def _read_candidate(
        self,
        url: str,
        phase: Phase,
        index: int,
        attempts: ArchiveDispositionAccumulator | None,
    ) -> LoadedFile:
        if attempts is None:
            return self.read(url)
        try:
            token = _ARCHIVE_READ_SINK.set(
                (
                    self,
                    url,
                    threading.get_ident(),
                    lambda root: attempts.observe(phase, index, root),
                )
            )
        except Exception:
            return self.read(url)
        try:
            return self.read(url)
        finally:
            with suppress(Exception):
                _ARCHIVE_READ_SINK.reset(token)

    def read_document(self, document: TextDocument) -> LoadedFile:
        """Read loose-file snapshots in the existing IO order."""
        bundle = start_bundle(self.read(document.url), min_chars=MIN_TEXT_CHARS)
        if isinstance(bundle, LoadedFile):
            return bundle
        scope = _COMPOSITION_SCOPE.get()
        attempt: tuple[_CompositionCapture, Phase, int] | None = None
        if (
            scope is not None
            and scope.owner is self
            and scope.thread_id == threading.get_ident()
            and scope.document is document
            and scope.phase is not None
            and scope.index is not None
        ):
            phase, index = scope.phase, scope.index
            capture = scope.capture
            attempt = (capture, phase, index)

            def observed(position: int, url: str, kind: Kind, file: LoadedFile) -> None:
                try:
                    if (
                        position >= len(document.required_urls)
                        or document.required_urls[position] != url
                    ):
                        raise ValueError("required exclusion differs from its actual relation")
                    capture.required.observe(phase, index, position, kind, file)
                except Exception:
                    scope.capture.required.fail()

            bundle = replace(bundle, required_sink=observed)
        candidate_index = attempt[2] if attempt is not None else 0
        address = (candidate_index - 1,) if candidate_index else ()
        for position, url in enumerate(document.required_urls):
            file = self._read_composed_part(document, address, "required", position, url)
            continuation = required_part(bundle, url, file)
            if isinstance(continuation, RequiredPartFailure):
                if attempt is not None:
                    gap_capture, gap_phase, gap_index = attempt
                    with suppress(Exception):
                        gap_capture.required_gap = RequiredPartGap(
                            phase=gap_phase,
                            index=gap_index,
                            required_index=position,
                            reason=continuation.reason,
                            file_sha256=None if file.oversize else file.sha256,
                        )
                return continuation.blocked
            bundle = continuation
        for position, url in enumerate(document.normative_urls):
            failure_reason = None
            try:
                annex = self.read(url)
            except ServiceUnavailableError:
                raise
            except Exception as exc:
                annex = None
                if attempt is not None:
                    with suppress(Exception):
                        failure_reason = read_exclusion_reason(exc)
            admission = normative_part(bundle, url, annex)
            if isinstance(admission, NormativeAnnexFailure):
                if attempt is not None:
                    gap_capture, gap_phase, gap_index = attempt
                    with suppress(Exception):
                        gap_capture.normative_gap = NormativeAnnexGap(
                            phase=gap_phase,
                            index=gap_index,
                            normative_index=position,
                            reason=failure_reason or admission.reason,
                            file_sha256=annex.sha256 if annex is not None else None,
                            kind=admission.kind,
                        )
                return admission.blocked
            if attempt is not None and annex is not None and annex.native_complete_standalone:
                capture, phase, index = attempt
                with suppress(Exception):
                    (part,) = annex.selection.parts
                    capture.admitted_annexes[phase, index, position] = AdmittedNormativeAnnex(
                        normative_index=position,
                        observed_kind=admission.kind,
                        file_sha256=part.sha256,
                        text_sha256=part.text_sha256,
                    )
            bundle = admission.bundle
        bundle = finish_normative(bundle)
        for position, url in enumerate(document.amendment_urls):
            file = self._read_composed_part(document, address, "amendment", position, url)
            amended = amendment_part(bundle, url, file)
            if isinstance(amended, RequiredPartFailure):
                if attempt is not None:
                    gap_capture, gap_phase, gap_index = attempt
                    with suppress(Exception):
                        gap_capture.amendment_gap = AmendmentPartGap(
                            phase=gap_phase,
                            index=gap_index,
                            amendment_index=position,
                            reason=amended.reason,
                            file_sha256=None if file.oversize else file.sha256,
                        )
                return amended.blocked
            bundle = amended
        for position, url in enumerate(document.extra_urls):
            try:
                loaded = self.read(url)
            except ServiceUnavailableError:
                raise
            except Exception as exc:
                log.warning("%s unreadable (%s: %s); skipped", url, type(exc).__name__, exc)
                bundle = supporting_failure(bundle, url, type(exc).__name__)
                if attempt is not None:
                    capture, phase, index = attempt
                    with suppress(Exception):
                        capture.exclude_support(phase, index, position, read_exclusion_reason(exc))
                continue
            composed = supporting_part(bundle, url, loaded)
            if attempt is not None and composed.missing != bundle.missing:
                capture, phase, index = attempt
                with suppress(Exception):
                    reason: SupportingExclusionReason = (
                        "download_size_limit"
                        if loaded.oversize
                        else "returned_no_text"
                        if loaded.text is None
                        else "draft_regulation"
                    )
                    capture.exclude_support(
                        phase, index, position, reason, None if loaded.oversize else loaded.sha256
                    )
            if (
                attempt is not None
                and (loaded.native_complete_standalone or loaded.complete_unlocated_doc_support)
                and (len(composed.evidence) == len(bundle.evidence) + 1)
            ):
                capture, phase, index = attempt
                part = composed.evidence[-1]
                with suppress(Exception):
                    capture.admitted_support[phase, index, position] = part.sha256, part.text_sha256
            if attempt is not None and len(composed.evidence) > len(bundle.evidence):
                capture, phase, index = attempt
                capture.composed_support[phase, index, position] = composed.evidence[
                    len(bundle.evidence) :
                ]
            bundle = composed
        return finish_bundle(bundle, document)

    def _read_composed_part(
        self,
        document: TextDocument,
        address: tuple[int, ...],
        relation: Literal["required", "amendment"],
        position: int,
        url: str,
    ) -> LoadedFile:
        try:
            return self.read(url)
        except Exception as error:
            with suppress(BaseException):
                scope = self._source_omissions.current()
                if scope is not None:
                    with scope.capture():
                        scope.part_read_failed(error, document, address, relation, position, url)
            raise

    def select_document(
        self, document: TextDocument, *, retain_routine_trace: bool = False
    ) -> tuple[TextDocument, LoadedFile]:
        dispositions: list[CandidateDisposition] = []
        attempts: ArchiveDispositionAccumulator | None = None
        with suppress(Exception):
            attempts = ArchiveDispositionAccumulator()
        with self._composition_capture_scope() as composition:
            selected, loaded, result, reason = self._select_document(
                document, dispositions, attempts, composition
            )
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
        if trace is not None and attempts is not None:
            with suppress(Exception):
                trace = attempts.enrich(trace)
        if trace is not None and composition is not None:
            with suppress(Exception):
                trace = composition.required.enrich(
                    trace, attempts.enrich if attempts is not None else None
                )
            if composition.normative_gap is not None:
                with suppress(Exception):
                    trace = CandidateDispositionTrace.model_validate(
                        {**trace.model_dump(), "normative_annex_gap": composition.normative_gap}
                    )
            if composition.required_gap is not None:
                with suppress(Exception):
                    trace = CandidateDispositionTrace.model_validate(
                        {**trace.model_dump(), "required_part_gap": composition.required_gap}
                    )
            if composition.amendment_gap is not None:
                with suppress(Exception):
                    trace = CandidateDispositionTrace.model_validate(
                        {**trace.model_dump(), "amendment_part_gap": composition.amendment_gap}
                    )
            if composition.supporting:
                with suppress(Exception):
                    trace = CandidateDispositionTrace.model_validate(
                        {
                            **trace.model_dump(),
                            "supporting_exclusions": SupportingPartExclusions(
                                rows=tuple(composition.supporting),
                                omitted=composition.supporting_omitted,
                            ),
                        }
                    )
        routine_trace = None
        if (
            not retain_routine_trace
            and trace is not None
            and trace.is_routine_singleton
            and trace.archive_members is None
        ):
            routine_trace, trace = trace, None
        archive_version_evidence: (
            ArchiveVersionConflictEvidence | ArchiveVersionDecisionEvidence | None
        ) = None
        if trace is not None and attempts is not None:
            archive_version_evidence = attempts.conflict_evidence
            if archive_version_evidence is None and trace.candidate_count == 1:
                archive_version_evidence = attempts.version_evidence
        if (
            trace is not None
            and composition is not None
            and composition.version_decision is not None
            and (
                (trace.archive_members is not None) == composition.version_decision_includes_archive
                or (trace.result == "selected" and composition.version_decision_includes_archive)
            )
        ):
            with suppress(Exception):
                enriched = CandidateDispositionTrace.model_validate(
                    {**trace.model_dump(), "version_decision": composition.version_decision}
                )
                if archive_version_evidence is not None:
                    _trace_with_archive_evidence(enriched, archive_version_evidence)
                trace = enriched
        base_trace = trace or routine_trace
        if base_trace is not None and composition is not None and composition.admitted_support:
            with suppress(Exception):
                evidence = _selected_admitted_support(base_trace, selected, loaded, composition)
                if evidence is not None:
                    enriched = CandidateDispositionTrace.model_validate(
                        {**base_trace.model_dump(), "admitted_support": evidence}
                    )
                    if archive_version_evidence is not None:
                        _trace_with_archive_evidence(enriched, archive_version_evidence)
                    trace = enriched
        base_trace = trace or routine_trace
        if base_trace is not None and composition is not None and composition.admitted_annexes:
            with suppress(Exception):
                annexes = _selected_admitted_annexes(base_trace, selected, loaded, composition)
                if annexes is not None:
                    enriched = CandidateDispositionTrace.model_validate(
                        {**base_trace.model_dump(), "admitted_normative_annexes": annexes}
                    )
                    if archive_version_evidence is not None:
                        _trace_with_archive_evidence(enriched, archive_version_evidence)
                    trace = enriched
        if trace is not None and attempts is not None:
            with suppress(Exception):
                packed = attempts.pack(trace)
                if packed is not trace and archive_version_evidence is not None:
                    _trace_with_archive_evidence(packed, archive_version_evidence)
                trace = packed
        base_trace = trace or routine_trace
        if base_trace is not None and composition is not None and composition.composed_support:
            with suppress(Exception):
                rows = _selected_composed_support(base_trace, selected, loaded, composition)
                parent = next(row for row in base_trace.dispositions if row.outcome == "selected")
                while rows:
                    try:
                        enriched = CandidateDispositionTrace.model_validate(
                            {
                                **base_trace.model_dump(),
                                "composed_support": ComposedSupport(
                                    phase=parent.phase, index=parent.index, rows=rows
                                ),
                            }
                        )
                        if archive_version_evidence is not None:
                            _trace_with_archive_evidence(enriched, archive_version_evidence)
                    except ValueError:
                        rows = rows[:-1]
                    else:
                        trace = enriched
                        break
        base_trace = trace or routine_trace
        if base_trace is not None and composition is not None and composition.composed_support:
            with suppress(Exception):
                enriched = _with_duplicate_support(
                    base_trace, selected, loaded, composition, archive_version_evidence
                )
                if enriched is not base_trace:
                    trace = enriched
        return selected, replace(
            loaded,
            archive_version_evidence=archive_version_evidence,
            selection=loaded.selection.model_copy(
                update={"policy_version": DOCUMENT_SELECTION_POLICY_VERSION}
            ),
            candidate_trace=trace,
        )

    @contextmanager
    def _composition_capture_scope(self) -> Iterator[_CompositionCapture | None]:
        capture = None
        token: Token[_CompositionScope | None] | None = None
        try:
            capture = _CompositionCapture(required=RequiredPartCapture())
            token = _COMPOSITION_SCOPE.set(_CompositionScope(self, threading.get_ident(), capture))
        except Exception:
            capture = None
        try:
            yield capture
        finally:
            if token is not None:
                with suppress(Exception):
                    _COMPOSITION_SCOPE.reset(token)

    def _read_composed_candidate(
        self, document: TextDocument, phase: Phase, index: int
    ) -> LoadedFile:
        scope = _COMPOSITION_SCOPE.get()
        if scope is None or scope.owner is not self or scope.thread_id != threading.get_ident():
            return self.read_document(document)
        try:
            token = _COMPOSITION_SCOPE.set(
                replace(scope, phase=phase, index=index, document=document)
            )
        except Exception:
            scope.capture.required.fail()
            return self.read_document(document)
        try:
            return self.read_document(document)
        finally:
            with suppress(Exception):
                _COMPOSITION_SCOPE.reset(token)

    def _select_document(
        self,
        document: TextDocument,
        dispositions: list[CandidateDisposition],
        attempts: ArchiveDispositionAccumulator | None,
        composition: _CompositionCapture | None,
    ) -> tuple[TextDocument, LoadedFile, CandidateTraceResult, CandidateTraceReason]:
        if document.compare_versions:
            resolved = self._compare_versions(document, dispositions, attempts, composition)
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
                main = self._read_candidate(candidate.url, "fallback", index, attempts)
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
            loaded = self._with_evidence(
                candidate, self._read_composed_candidate(candidate, "fallback", index)
            )
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
        self,
        document: TextDocument,
        dispositions: list[CandidateDisposition],
        attempts: ArchiveDispositionAccumulator | None,
        composition: _CompositionCapture | None,
    ) -> tuple[TextDocument, LoadedFile, CandidateTraceResult, CandidateTraceReason] | None:
        candidates: list[tuple[TextDocument, LoadedFile]] = []
        prepared: list[DocumentCandidate] = []
        candidate_indices: list[int] = []
        incomplete: dict[int, DocumentSelection] = {}
        unpack_blocked: set[int] = set()
        normative_blocked: set[int] = set()
        conflicts: dict[int, DocumentSelection] = {}
        observed: dict[int, tuple[DocumentPart, ...]] = {}
        decisions: list[CandidateDisposition] = []
        sources = (document, *document.alternatives)
        compare_packages = len(sources) > 1 and any(
            item.required_urls or item.normative_urls or item.amendment_urls for item in sources
        )
        for index, candidate in enumerate(sources):
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
                file = self._read_candidate(candidate.url, "version_comparison", index, attempts)
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
            if file.sha256 is not None:
                if file.archive_unpack_blocked:
                    unpack_blocked.add(index)
                if file.archive_unpack_blocked or file.selection.has_normative_pdf_page_failure:
                    incomplete[index] = file.selection
            item = DocumentCandidate(
                candidate.url, file.text, file.selection.parts, file.selection.observed_parts
            )
            is_bill = item.is_bill
            if compare_packages and is_bill and index not in incomplete:
                if candidate.required_urls or candidate.normative_urls or candidate.amendment_urls:
                    normative = candidate.model_copy(update={"extra_urls": ()})
                    try:
                        composed = self.read_document(normative)
                    except ServiceUnavailableError:
                        raise
                    except Exception:
                        composed = LoadedFile(None, selection=DocumentSelection(state="waiting"))
                    if composed.text is None or composed.selection.state in (
                        "waiting",
                        "ambiguous",
                    ):
                        if file.sha256 is not None:
                            incomplete[index] = composed.selection
                            normative_blocked.add(index)
                        is_bill = False
                    else:
                        item = DocumentCandidate(
                            candidate.url,
                            composed.text,
                            composed.selection.parts,
                            tuple(dict.fromkeys((*item.observed_parts, *composed.selection.parts))),
                        )
                if is_bill:
                    item = item.with_normative_parts()
            if (
                file.selection.state == "ambiguous"
                and "draft_version_conflict" in file.selection.limitations
            ):
                conflicts[index] = file.selection
            if index in incomplete or index in conflicts or is_bill:
                observed[index] = item.observed_parts or item.parts
            if is_bill:
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
        if conflicts:
            dispositions.extend(
                item.model_copy(update={"outcome": "policy_excluded", "reason": "version_conflict"})
                if item.index in conflicts or item.index in candidate_indices
                else item
                for item in decisions
            )
            selection = DocumentSelection(
                state="ambiguous",
                limitations=("draft_version_conflict",),
                parts=tuple(part for conflict in conflicts.values() for part in conflict.parts),
                observed_parts=tuple(part for parts in observed.values() for part in parts),
                rejected=tuple(
                    reason for conflict in conflicts.values() for reason in conflict.rejected
                ),
            )
            return document, LoadedFile(None, selection=selection), "ambiguous", "version_conflict"
        if incomplete and len(incomplete.keys() | set(candidate_indices)) > 1:
            dispositions.extend(
                item.model_copy(
                    update={
                        "outcome": "unclassified",
                        "reason": (
                            "archive_member_unpack_failed"
                            if item.index in unpack_blocked
                            else "normative_part_read_incomplete"
                            if item.index in normative_blocked
                            else "pdf_page_extraction_failed"
                            if item.index in incomplete
                            else "version_comparison_deferred"
                        ),
                    }
                )
                if item.index in incomplete or item.index in candidate_indices
                else item
                for item in decisions
            )
            selection = DocumentSelection(
                state="waiting",
                parts=tuple(part for failed in incomplete.values() for part in failed.parts),
                observed_parts=tuple(part for parts in observed.values() for part in parts),
                rejected=(
                    *(
                        ("Archive draft could not be unpacked during version comparison",)
                        if unpack_blocked
                        else ()
                    ),
                    *(
                        ("A declared normative part could not be read during version comparison",)
                        if normative_blocked
                        else ()
                    ),
                    *(
                        ("PDF page extraction failed during version comparison",)
                        if incomplete.keys() - unpack_blocked - normative_blocked
                        else ()
                    ),
                ),
            )
            return (
                document,
                LoadedFile(None, selection=selection),
                "waiting",
                "version_input_incomplete",
            )
        choice = select_candidate_versions(CandidateSet(tuple(prepared)))
        if choice is None:
            dispositions.extend(decisions)
            return None
        includes_archive = any(
            part.members for item in prepared for part in (*item.parts, *item.observed_parts)
        )
        if (
            composition is not None
            and not compare_packages
            and 2 <= len(prepared) <= MAX_VERSION_DECISION_ROWS
            and all(item.parts for item in prepared)
            and (
                not includes_archive
                or isinstance(choice, AmbiguousCandidates)
                or (
                    attempts is not None
                    and all(
                        attempts.has_native_comparison_root(index, file.sha256)
                        for index, (_, file) in zip(candidate_indices, candidates, strict=True)
                    )
                )
            )
        ):
            comparison = choice.comparison
            with suppress(Exception):
                composition.version_decision = VersionDecisionEvidence(
                    compared_indices=tuple(candidate_indices),
                    retained_indices=tuple(
                        candidate_indices[index] for index in comparison.retained_indices
                    ),
                    outcome=(
                        "distinct_policy_identity"
                        if isinstance(choice, AmbiguousCandidates)
                        else "single_survivor"
                        if len(comparison.retained_indices) == 1
                        else "matching_policy_identity"
                    ),
                    date_filter=tuple(date.isoformat() for date in comparison.date_filter)
                    if comparison.date_filter is not None
                    else None,
                )
                composition.version_decision_includes_archive = includes_archive
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
        selected_index = candidate_indices[choice.index]
        loaded = self._with_evidence(
            candidate,
            self._read_composed_candidate(candidate, "version_comparison", selected_index),
        )
        dispositions.extend(
            item.model_copy(update={"outcome": "selected", "reason": "version_choice"})
            if item.index == selected_index
            else item
            for item in decisions
        )
        return (
            candidate,
            replace(
                loaded,
                selection=loaded.selection.model_copy(
                    update={"observed_parts": choice.observed_parts}
                ),
                candidate_trace=None,
            ),
            "selected",
            "version_choice",
        )

    def _with_evidence(self, document: TextDocument, file: LoadedFile) -> LoadedFile:
        return finalize_selection(document, file)

    def load_scan(self, url: str, *, cover_letter: bool) -> ScannedDocument | None:
        """Return bounded PDF bytes with separate original and exact-input identities."""
        downloaded = self._download(url)
        if downloaded is None:
            return None
        data = downloaded.data
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
        source_sha = downloaded.artifact.sha256
        scan = ScannedDocument(
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
        observe_read_timing(self, "fresh")
        return scan

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

    def _fetch(
        self,
        url: str,
        archive_sink: Callable[[ArchiveRootObservation], None] | None = None,
        *,
        max_bytes: int | None = None,
    ) -> _CachedDocument:
        downloaded = self._download(url, max_bytes=max_bytes)
        if downloaded is None:
            return _CachedDocument(LoadedFile(None, oversize=True))
        data, artifact = downloaded.data, downloaded.artifact
        started = time.perf_counter()
        capture = None
        invocation: PhysicalInvocation | None = None
        archive: ArchiveRootObservation | None = None
        completed = False
        native_standalone = False
        complete_unlocated_doc = False
        try:
            with (
                optional_archive_policy_capture() as capture,
                self._physical_invocation(artifact) as invocation,
            ):
                first_pdf_page = None
                complete_pdf_pages = None
                word_formula_source = None
                archive_unpack_blocked = False
                if isinstance(self._extractor, ProvenanceTextExtractor):
                    observed = self._extractor.extract_document_with_provenance(data)
                    if self._capture_word_formulas is not None and urlparse(
                        url
                    ).path.lower().endswith((".docx", ".docm")):
                        with suppress(Exception):
                            word_formula_source = self._capture_word_formulas(data, observed)
                    if capture is not None and capture.native_single_part:
                        with suppress(Exception):
                            native_standalone = complete_standalone_root(observed, artifact.sha256)
                            if not native_standalone:
                                complete_unlocated_doc = complete_standalone_root(
                                    observed, artifact.sha256, allow_unlocated_doc=True
                                )
                    first_pdf_page = observed.first_pdf_page
                    complete_pdf_pages = observed.complete_pdf_pages
                    text = observed.document.text
                    selection = observed.document.selection
                    archive_unpack_blocked = observed.document.archive_unpack_blocked
                    with self._lock:
                        self._extractions[observed.result.artifact_sha256] = observed.result
                    if invocation is not None:
                        try:
                            invocation.bind_extraction(observed.result)
                        except BaseException:
                            if self._physical is not None:
                                self._physical_note_failure("extraction_callback_unavailable")
                    scope = self._source_omissions.current()
                    if scope is not None and observed.result.artifact_sha256 == artifact.sha256:
                        with scope.capture():
                            scope.extraction(observed.result)
                elif isinstance(self._extractor, StructuredTextExtractor):
                    extracted = self._extractor.extract_document(data)
                    text = extracted.text
                    selection = extracted.selection
                    archive_unpack_blocked = extracted.archive_unpack_blocked
                else:
                    text = self._extractor.extract(data)
                    selection = DocumentSelection(
                        parts=(
                            DocumentPart(
                                sha256=artifact.sha256,
                                text=text,
                                text_end=len(text),
                                text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                            ),
                        )
                    )
                selection = selection.model_copy(
                    update={
                        "parts": tuple(
                            part.model_copy(update={"url": url}) for part in selection.parts
                        ),
                        "observed_parts": tuple(
                            part.model_copy(update={"url": url})
                            for part in selection.observed_parts
                        ),
                    }
                )
                digest = artifact.sha256
                pages = complete_pdf_pages or self._extractor.pages(data)
                log.info(
                    "%s: %d chars extracted in %.1fs", url, len(text), time.perf_counter() - started
                )
                has_text = len(text.strip()) >= MIN_TEXT_CHARS
                if not has_text:
                    log.warning(
                        "%s yielded almost no text (%d chars, %d pages)", url, len(text), pages
                    )
                loaded = LoadedFile(
                    text if has_text else None,
                    pages=pages,
                    sha256=digest,
                    selection=selection,
                    first_pdf_page=first_pdf_page,
                    complete_pdf_pages=complete_pdf_pages,
                    word_formula_source=word_formula_source,
                    native_complete_standalone=native_standalone,
                    complete_unlocated_doc_support=complete_unlocated_doc,
                    archive_unpack_blocked=archive_unpack_blocked,
                )
                completed = True
        finally:
            try:
                archive = ArchiveRootObservation(
                    artifact,
                    invocation.observation if invocation is not None else None,
                    capture.decisions if capture is not None and completed else None,
                    completed,
                    native_standalone=native_standalone,
                )
                if archive_sink is not None:
                    archive_sink(archive)
            except Exception:
                archive = None
        return _CachedDocument(
            loaded, invocation.observation if invocation is not None else None, archive
        )

    def _download(self, url: str, *, max_bytes: int | None = None) -> _DownloadedDocument | None:
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
            limit = self._max_bytes if max_bytes is None else min(self._max_bytes, max_bytes)
            data = download(url, max_bytes=limit)
            if max_bytes is not None and len(data) > limit:
                raise AttachmentTooLargeError(url, limit)
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
        artifact = ArtifactIdentity(sha256=digest, byte_size=len(data))
        with self._lock:
            self._known_bodies.setdefault(digest, len(data))
        scope = self._source_omissions.current()
        if scope is not None:
            with scope.capture():
                scope.artifact(artifact)
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
        observe_read_timing(self, "fresh")
        return _DownloadedDocument(data, artifact)

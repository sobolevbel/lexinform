"""Capture the resolver's reached facts without re-running its admission decisions."""

import datetime as dt
import hashlib
import logging
import platform
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from functools import wraps
from typing import Concatenate, Literal

from lexinform import senate_documents as inspection_code
from lexinform.errors import OrkaUnreachableError, ServiceUnavailableError
from lexinform.extraction_records import component_code
from lexinform.metadata_urls import metadata_url_decision
from lexinform.models import (
    Bill,
    DocumentPart,
    DocumentSelection,
    DocumentSelectionRecord,
    ExtractionResult,
    PrintInfo,
    SourceObservationIdentity,
    Stage,
    TextDocument,
)
from lexinform.models.senate import SenateResolutionEvidence
from lexinform.models.senate_inspection import (
    InspectionOutcome,
    SenateCandidateInspection,
    SenateInspectionCandidate,
    SenateInspectionComponents,
    SenateInspectionContext,
    SenateInspectionDescriptor,
    SenateInspectionEvidence,
    SenateInspectionOwner,
    SenateInspectionPayload,
    SenateInspectionPhase,
    SenateInspectionRecord,
    SenateInspectionResult,
    SenateInspectionStage,
    SenateInspectionUrl,
)
from lexinform.services.document_history import DocumentSourceRead
from lexinform.services.documents import LoadedFile, TextLoader
from lexinform.services.senate_history import SenateInspectionObservation

log = logging.getLogger(__name__)
Phase = Literal["word", "pdf"]


def captured_senate_url(value: str) -> SenateInspectionUrl:
    decision = metadata_url_decision(value)
    if decision.reason is None:
        return SenateInspectionUrl.of(value)
    if decision.requested_url_sha256 is None:
        raise ValueError("an unencodable Senate metadata URL has no exact UTF-8 identity")
    return SenateInspectionUrl(
        value=None, state="omitted_unsafe", sha256=decision.requested_url_sha256
    )


def _capture_fact[**P](
    method: Callable[Concatenate[SenateInspectionCapture, P], None],
) -> Callable[Concatenate[SenateInspectionCapture, P], None]:
    @wraps(method)
    def record(self: SenateInspectionCapture, /, *args: P.args, **kwargs: P.kwargs) -> None:
        if self._capture_failure is None:
            try:
                method(self, *args, **kwargs)
            except BaseException as exc:
                self._capture_failure = type(exc).__name__[:128]

    return record


class SenateInspectionCapture:
    def __init__(
        self,
        owner: Bill,
        info: PrintInfo,
        stage: Stage,
        *,
        titles: tuple[str, ...],
        passed_on: dt.date | None,
        resolver_source: str,
        observe: Callable[[SenateInspectionObservation], None],
    ) -> None:
        self._owner = SenateInspectionOwner(term=owner.term, number=owner.number)
        self._context = SenateInspectionContext(
            resolution_print=SenateInspectionOwner(term=info.term, number=info.number),
            print_title=info.title,
            document_date=info.document_date,
            stage=SenateInspectionStage.model_validate(
                {
                    "stage_type": stage.stage_type,
                    "stage_name": stage.stage_name,
                    "date": stage.date,
                    "position": stage.position,
                    "print_number": stage.print_number,
                }
            ),
            passed_on=passed_on,
            titles=titles,
            expected_subjects=(),
        )
        self._components = SenateInspectionComponents(
            resolver_sha256=component_code(resolver_source),
            inspection_sha256=component_code(inspection_code.__file__),
            python=platform.python_version(),
        )
        self._descriptors = tuple(
            SenateInspectionDescriptor(
                index=index,
                print_number=item.print_number,
                name=item.name,
                url=captured_senate_url(item.url),
            )
            for index, item in enumerate(info.attachments)
        )
        self._facts: dict[int, SenateCandidateInspection] = {}
        self._physical: dict[int, dict[str, object]] = {}
        self._sources: dict[str, DocumentSourceRead] = {}
        self._extractions: dict[str, ExtractionResult] = {}
        self._phases: list[SenateInspectionPhase] = []
        self._observe = observe
        self._active: tuple[int, Phase] | None = None
        self._terminal: InspectionOutcome = "all_rejected"
        self._chosen: int | None = None
        self._capture_failure: str | None = None

    @_capture_fact
    def subjects(self, subjects: tuple[str, ...]) -> None:
        self._context = self._context.model_copy(update={"expected_subjects": subjects})

    @_capture_fact
    def skip(self, index: int, reason: Literal["foreign_print", "unsupported_extension"]) -> None:
        self._facts.setdefault(
            index,
            SenateCandidateInspection(
                phase="filter", attempted=False, state="unattempted", reason=reason
            ),
        )

    @_capture_fact
    def start(self, index: int, phase: Phase) -> None:
        self._active = index, phase
        self._facts.pop(index, None)

    @_capture_fact
    def failed(
        self, error: BaseException, reason: Literal["missing", "read_error", "source_unavailable"]
    ) -> None:
        if self._active is None:
            return
        index, phase = self._active
        self._facts[index] = SenateCandidateInspection(
            phase=phase,
            attempted=True,
            state="read_error",
            reason=reason,
            error_class=type(error).__name__,
        )
        self._active = None

    @_capture_fact
    def read(self, loader: TextLoader, url: str, loaded: LoadedFile) -> None:
        if self._active is None:
            return
        index, _ = self._active
        artifact, read, extraction = loader.file_origins(url, loaded)
        values: dict[str, object] = {
            "pages": loaded.pages,
            "reader_text_sha256": hashlib.sha256(loaded.text.encode()).hexdigest()
            if loaded.text is not None
            else None,
        }
        if artifact is not None:
            values.update(
                artifact_sha256=artifact.sha256,
                artifact_bytes=artifact.byte_size,
                extraction_id=extraction.result_id if extraction else None,
            )
            if not SenateInspectionUrl.safe(url) or not metadata_url_decision(url).admitted:
                if extraction is not None:
                    previous_extraction = self._extractions.setdefault(
                        extraction.result_id, extraction
                    )
                    if previous_extraction != extraction:
                        raise ValueError(
                            "one captured Senate extraction cannot change its metadata"
                        )
                self._physical[index] = values
                return
            selection = DocumentSelection(
                question="senate_candidate_inspection",
                policy_version="senate-source-observation-v1",
                parts=(
                    DocumentPart(
                        url=url,
                        sha256=artifact.sha256,
                        pages=loaded.pages,
                        text_sha256=str(values["reader_text_sha256"] or ""),
                    ),
                ),
            )
            source = DocumentSourceRead(
                TextDocument(url=url, kind="senate_amendments"),
                DocumentSelectionRecord.from_selection(selection),
                (artifact,),
                reads=(read,) if read else (),
                extractions=(extraction,) if extraction else (),
            )
            previous = self._sources.setdefault(source.selection.selection_id, source)
            if (previous.document, previous.selection, previous.artifacts) != (
                source.document,
                source.selection,
                source.artifacts,
            ):
                raise ValueError(
                    "one captured Senate selection cannot change its physical snapshot"
                )
            reads = {item.read_id: item for item in previous.reads}
            for receipt in source.reads:
                if reads.setdefault(receipt.read_id, receipt) != receipt:
                    raise ValueError("one captured Senate read cannot change its metadata")
            extractions = {item.result_id: item for item in previous.extractions}
            for result in source.extractions:
                if extractions.setdefault(result.result_id, result) != result:
                    raise ValueError("one captured Senate extraction cannot change its metadata")
            self._sources[source.selection.selection_id] = replace(
                source,
                reads=tuple(reads[key] for key in sorted(reads)),
                extractions=tuple(extractions[key] for key in sorted(extractions)),
            )
            identity = SourceObservationIdentity(
                term=self._owner.term,
                number=self._owner.number,
                purpose="analysis",
                document=source.document,
                selection_id=source.selection.selection_id,
            )
            values.update(
                artifact_sha256=artifact.sha256,
                artifact_bytes=artifact.byte_size,
                artifact_read_id=read.read_id if read else None,
                extraction_id=extraction.result_id if extraction else None,
                selection_id=source.selection.selection_id,
                source_observation_id=identity.observation_id,
            )
        self._physical[index] = values

    @_capture_fact
    def inspected(
        self,
        evidence: SenateResolutionEvidence,
        *,
        word_eof: bool,
        reason: str,
        normalized: str | None = None,
        visual: bool = False,
    ) -> None:
        if self._active is None:
            return
        index, phase = self._active
        self._facts[index] = SenateCandidateInspection.model_validate(
            {
                **self._physical.get(index, {}),
                "phase": phase,
                "attempted": True,
                "state": "inspected",
                "reason": reason or "qualified_text",
                "word_eof": word_eof,
                "inspection": SenateInspectionEvidence.model_validate(
                    {
                        "limitation": evidence.limitation,
                        "outcome": evidence.outcome,
                        "subject": evidence.subject,
                        "adopted_on": evidence.adopted_on,
                        "passed_on": evidence.passed_on,
                        "complete": evidence.complete,
                    }
                ),
                "normalized_resolution_sha256": hashlib.sha256(normalized.encode()).hexdigest()
                if normalized is not None
                else None,
                "visual_eligible": visual,
            }
        )
        self._active = None

    @_capture_fact
    def phase(self, phase: Phase, *, incomplete: bool) -> None:
        qualified = tuple(
            index
            for index, fact in sorted(self._facts.items())
            if fact.phase == phase and fact.reason == "qualified_text"
        )
        self._phases.append(
            SenateInspectionPhase(
                phase=phase,
                incomplete=incomplete,
                qualified_indices=qualified,
                equivalence_groups=tuple(
                    sorted(
                        {
                            str(self._facts[index].normalized_resolution_sha256)
                            for index in qualified
                        }
                    )
                ),
            )
        )

    @_capture_fact
    def result(self, outcome: InspectionOutcome, index: int | None = None) -> None:
        self._terminal, self._chosen = outcome, index

    def finish(self, document: TextDocument | None, error: BaseException | None) -> None:
        try:
            self._finish(document, error)
        except BaseException as exc:
            with suppress(BaseException):
                log.warning("Senate inspection capture failed (%s)", type(exc).__name__[:128])

    def _finish(self, document: TextDocument | None, error: BaseException | None) -> None:
        if self._capture_failure is not None:
            log.warning("Senate inspection capture failed (%s)", self._capture_failure)
            return
        if error is not None:
            self._terminal, self._chosen = "propagated_failure", None
            if self._active is not None:
                index, phase = self._active
                self._facts[index] = SenateCandidateInspection.model_validate(
                    {
                        **self._physical.get(index, {}),
                        "phase": phase,
                        "attempted": True,
                        "state": "inspection_error" if index in self._physical else "read_error",
                        "reason": "inspection_error" if index in self._physical else "read_error",
                        "error_class": type(error).__name__,
                    }
                )
        outage = isinstance(error, ServiceUnavailableError) or (
            isinstance(error, OrkaUnreachableError) and not error.file_is_missing
        )
        fallback = (
            "after_outage"
            if outage
            else "after_failure"
            if error is not None
            else "stage_not_eligible"
            if self._terminal == "guard_rejected"
            else "word_precedence"
        )
        universe = tuple(
            SenateInspectionCandidate(
                descriptor=descriptor,
                inspection=self._facts.get(descriptor.index)
                or SenateCandidateInspection.model_validate(
                    {
                        "phase": "filter",
                        "attempted": False,
                        "state": "unattempted",
                        "reason": fallback,
                    }
                ),
            )
            for descriptor in self._descriptors
        )
        payload = SenateInspectionPayload(
            owner=self._owner,
            context=self._context,
            components=self._components,
            universe=universe,
            phases=tuple(self._phases),
            result=SenateInspectionResult.model_validate(
                {
                    "outcome": self._terminal,
                    "descriptor_index": self._chosen,
                    "identity": document.senate.model_dump()
                    if document and document.senate
                    else None,
                    "error_class": type(error).__name__ if error else None,
                }
            ),
        )
        links = payload.physical_links()
        self._observe(
            SenateInspectionObservation(
                SenateInspectionRecord.of(payload, links),
                links,
                tuple(self._sources[key] for key in sorted(self._sources)),
                tuple(self._extractions[key] for key in sorted(self._extractions)),
            )
        )

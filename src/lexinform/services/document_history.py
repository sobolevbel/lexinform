"""Calling transactions record known physical metadata independently of body retention."""

import datetime as dt
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal

from lexinform.committee_recommendation import CommitteeRecommendationOrigin, report_evidence
from lexinform.document_editions import candidate_edition
from lexinform.errors import SourceHistoryPersistenceError
from lexinform.models import (
    ArtifactIdentity,
    ArtifactMetadata,
    ArtifactReadIdentity,
    ArtifactReadReceipt,
    Bill,
    CandidateDispositionTrace,
    DocumentSelectionRecord,
    ExtractionResult,
    SelectionArtifactReference,
    SelectionExtractionReference,
    SourceObservation,
    TextDocument,
)
from lexinform.models.artifacts import SelectionPartScope
from lexinform.models.candidate_trace import (
    SejmAdoptedText,
    SejmCommitteeRecommendation,
    SejmOriginalProposal,
    SejmPrintRepresentation,
    SejmSupersededDraft,
)
from lexinform.models.document_editions import DocumentEdition
from lexinform.models.document_history import SourceObservationIdentity
from lexinform.models.sejm import SejmAdoptionOrigin, SejmProposalOrigin, SejmRepresentationOrigin
from lexinform.models.source_omissions import (
    BillOmissionOwner,
    RclOmissionOwner,
    SenateOmissionOwner,
    SourceMetadataOmission,
    SourceReferenceOmissionOwner,
)
from lexinform.ports import BillRepository, SourceHistoryPersistence
from lexinform.source_omission_frames import source_omission_owner

log = logging.getLogger(__name__)


def persist_source_history(
    repo: BillRepository, history: SourceHistoryPersistence, *, recorded_at: dt.datetime
) -> None:
    """Contain new observation-hook failures without changing unrelated cancellation handling."""
    try:
        history.persist_source_history(repo, recorded_at=recorded_at)
    except BaseException as exc:
        raise SourceHistoryPersistenceError(type(exc).__name__) from None


@dataclass(frozen=True)
class PrintRepresentationRead:
    origin: SejmRepresentationOrigin
    trace: CandidateDispositionTrace
    docx: ArtifactReadIdentity
    extraction: ExtractionResult
    core_sha256: str
    core_chars: int


@dataclass(frozen=True)
class DocumentSourceRead:
    document: TextDocument
    selection: DocumentSelectionRecord
    artifacts: tuple[ArtifactIdentity, ...]
    reads: tuple[ArtifactReadIdentity, ...] = ()
    extractions: tuple[ExtractionResult, ...] = ()
    candidate_trace: CandidateDispositionTrace | None = None
    sejm_origin: SejmAdoptionOrigin | SejmProposalOrigin | None = None
    committee_draft: DocumentSelectionRecord | None = None
    representation: PrintRepresentationRead | None = None


@dataclass(frozen=True)
class PhysicalSourceRead:
    artifacts: tuple[ArtifactIdentity, ...]
    reads: tuple[ArtifactReadIdentity, ...] = ()
    extractions: tuple[ExtractionResult, ...] = ()
    omission: SourceMetadataOmission | None = None


type SourceReadCapture = DocumentSourceRead | PhysicalSourceRead


@dataclass(frozen=True)
class SourceHistoryPair:
    first: SourceHistoryPersistence
    second: SourceHistoryPersistence

    def persist_source_history(self, repo: BillRepository, *, recorded_at: dt.datetime) -> None:
        self.first.persist_source_history(repo, recorded_at=recorded_at)
        self.second.persist_source_history(repo, recorded_at=recorded_at)


def record_committee_recommendation(
    repo: BillRepository,
    bill: Bill,
    origin: CommitteeRecommendationOrigin,
    left: SourceReadCapture | None,
    right: SourceReadCapture | None,
    first_pdf_page: str | None,
) -> None:
    if (
        not isinstance(left, DocumentSourceRead)
        or not isinstance(right, DocumentSourceRead)
        or left.candidate_trace is None
        or first_pdf_page is None
        or origin.report.print_number is None
        or origin.resolution.print_number is None
        or origin.report.date is None
        or origin.resolution.date is None
        or right.document.senate is None
        or right.document.senate.scan
    ):
        return
    left_reads = [read for read in left.reads if read.requested_url == left.document.url]
    right_reads = [
        read
        for read in right.reads
        if (read.requested_url, read.sha256) == (right.document.url, right.document.senate.sha256)
    ]
    if len(left_reads) != 1 or len(right_reads) != 1:
        return
    (report_read,), (target_read,) = left_reads, right_reads
    report_observation = SourceObservationIdentity(
        term=bill.term,
        number=bill.number,
        purpose="analysis",
        document=left.document,
        selection_id=left.selection.selection_id,
    )
    target_observation = SourceObservationIdentity(
        term=bill.term,
        number=bill.number,
        purpose="analysis",
        document=right.document,
        selection_id=right.selection.selection_id,
    )
    try:
        heading = report_evidence(
            first_pdf_page, origin.report.print_number, origin.resolution.print_number
        )
        proof = SejmCommitteeRecommendation(
            term=bill.term,
            number=bill.number,
            report=origin.report.print_number,
            resolution=origin.resolution.print_number,
            report_date=origin.report.date,
            resolution_date=origin.resolution.date,
            heading_sha256=heading["heading_sha256"],
            report_sha256=report_read.sha256,
            resolution_sha256=target_read.sha256,
            report_read_id=report_read.read_id,
            resolution_read_id=target_read.read_id,
            resolution_observation_id=target_observation.observation_id,
        )
        trace = type(left.candidate_trace).model_validate(
            {**left.candidate_trace.model_dump(), "sejm_committee_recommendation": proof}
        )
        repo.save_candidate_disposition_trace(report_observation.observation_id, trace)
    except ValueError:
        log.debug("Sejm committee recommendation proof failed validation")


def record_source_read(
    repo: BillRepository,
    source: SourceReadCapture,
    *,
    term: int,
    number: str,
    purpose: Literal["prefilter", "analysis", "review"],
    recorded_at: dt.datetime,
) -> None:
    """Record a consumer's source metadata inside its caller-owned transaction."""
    if isinstance(source, PhysicalSourceRead):
        record_artifacts(repo, source.artifacts, recorded_at=recorded_at)
        record_artifact_reads(repo, source.reads, recorded_at=recorded_at)
        for extraction in source.extractions:
            repo.save_extraction_result(extraction, recorded_at=recorded_at)
        if source.omission is not None:
            owner = source.omission.owner
            if not isinstance(owner, (BillOmissionOwner, SourceReferenceOmissionOwner)) or (
                owner.term,
                owner.number,
                owner.purpose,
            ) != (term, number, purpose):
                raise ValueError("source omission must match its actual calling owner and purpose")
        return
    observation = SourceObservation(
        term=term,
        number=number,
        purpose=purpose,
        document=source.document,
        selection_id=source.selection.selection_id,
        recorded_at=recorded_at,
    )
    record_document_source(
        repo,
        source.selection,
        observation,
        source.artifacts,
        reads=source.reads,
        extractions=source.extractions,
        candidate_trace=source.candidate_trace,
        sejm_origin=source.sejm_origin,
    )
    if source.representation is not None:
        _record_print_representation(repo, source, observation, source.representation)
    if source.committee_draft is not None:
        record_document_source(
            repo,
            source.committee_draft,
            observation.model_copy(update={"selection_id": source.committee_draft.selection_id}),
            source.artifacts,
            reads=source.reads,
            extractions=source.extractions,
        )


def record_source_omission(
    repo: BillRepository, record: SourceMetadataOmission, *, recorded_at: dt.datetime
) -> None:
    owner = record.owner
    if not isinstance(owner, RclOmissionOwner):
        enrolled = repo.bill_exists(owner.term, owner.number)
        if enrolled and isinstance(owner, SourceReferenceOmissionOwner):
            record = source_omission_owner(
                record,
                BillOmissionOwner(
                    term=owner.term,
                    number=owner.number,
                    purpose=owner.purpose,
                ),
            )
        elif not enrolled and isinstance(owner, (BillOmissionOwner, SenateOmissionOwner)):
            record = source_omission_owner(
                record,
                SourceReferenceOmissionOwner(
                    term=owner.term,
                    number=owner.number,
                    purpose=owner.purpose if isinstance(owner, BillOmissionOwner) else "analysis",
                ),
            )
    repo.save_source_metadata_omission(record, recorded_at=recorded_at)


def record_artifacts(
    repo: BillRepository, artifacts: tuple[ArtifactIdentity, ...], *, recorded_at: dt.datetime
) -> None:
    """Write complete-body metadata inside the caller's transaction."""
    for artifact in artifacts:
        repo.save_artifact_metadata(
            ArtifactMetadata(
                sha256=artifact.sha256, byte_size=artifact.byte_size, recorded_at=recorded_at
            )
        )


def record_artifact_reads(
    repo: BillRepository, reads: tuple[ArtifactReadIdentity, ...], *, recorded_at: dt.datetime
) -> None:
    """Persist known complete-body receipts inside the calling transaction."""
    record_artifacts(repo, reads, recorded_at=recorded_at)
    for read in reads:
        repo.save_artifact_read(
            ArtifactReadReceipt(
                sha256=read.sha256,
                byte_size=read.byte_size,
                requested_url=read.requested_url,
                recorded_at=recorded_at,
            )
        )


def _record_print_representation(
    repo: BillRepository,
    source: DocumentSourceRead,
    observation: SourceObservation,
    evidence: PrintRepresentationRead,
) -> None:
    edition = repo.get_selection_edition(source.selection.selection_id)
    if edition is None or len(source.selection.selection.parts) != 1:
        return
    (part,) = source.selection.selection.parts
    try:
        proof = SejmPrintRepresentation(
            profile="sejm-print-core-live-v1",
            term=evidence.origin.term,
            number=evidence.origin.number,
            print_metadata_sha256=evidence.origin.metadata_sha256,
            pdf_sha256=part.sha256,
            docx_sha256=evidence.docx.sha256,
            pdf_text_sha256=part.text_sha256,
            docx_text_sha256=evidence.extraction.output_text_sha256,
            core_sha256=evidence.core_sha256,
            core_chars=evidence.core_chars,
            selection_id=source.selection.selection_id,
            edition_id=edition.edition_id,
            docx_extraction_id=evidence.extraction.result_id,
        )
        trace = CandidateDispositionTrace.model_validate(
            {**evidence.trace.model_dump(), "sejm_print_representation": proof}
        )
        with repo.atomic():
            record_artifacts(repo, (evidence.docx,), recorded_at=observation.recorded_at)
            record_artifact_reads(repo, (evidence.docx,), recorded_at=observation.recorded_at)
            repo.save_extraction_result(evidence.extraction, recorded_at=observation.recorded_at)
            repo.save_candidate_disposition_trace(observation.observation_id, trace)
    except ValueError:
        log.debug("Sejm print representation proof failed validation")


def record_document_source(
    repo: BillRepository,
    selection: DocumentSelectionRecord,
    observation: SourceObservation,
    artifacts: tuple[ArtifactIdentity, ...],
    *,
    reads: tuple[ArtifactReadIdentity, ...] = (),
    extractions: tuple[ExtractionResult, ...] = (),
    candidate_trace: CandidateDispositionTrace | None = None,
    sejm_origin: SejmAdoptionOrigin | SejmProposalOrigin | None = None,
) -> None:
    """Write the source and known outer-body links inside the caller's transaction."""
    record_artifacts(repo, artifacts, recorded_at=observation.recorded_at)
    record_artifact_reads(repo, reads, recorded_at=observation.recorded_at)
    repo.save_source_observation(selection, observation)
    origin_trace = None
    if candidate_trace is not None:
        if (
            sejm_origin is not None
            and candidate_trace.is_routine_singleton
            and candidate_trace.archive_members is None
        ):
            origin_trace = candidate_trace
        else:
            repo.save_candidate_disposition_trace(observation.observation_id, candidate_trace)
    edition = candidate_edition(selection, recorded_at=observation.recorded_at)
    if edition is not None:
        repo.save_selection_edition(selection.selection_id, edition)
    for extraction in extractions:
        repo.save_extraction_result(extraction, recorded_at=observation.recorded_at)
        repo.save_selection_extraction(
            SelectionExtractionReference(
                selection_id=selection.selection_id,
                extraction_id=extraction.result_id,
                artifact_sha256=extraction.artifact_sha256,
            )
        )
    scopes: tuple[SelectionPartScope, ...] = ("parts", "observed_parts")
    for scope in scopes:
        parts = (
            selection.selection.parts if scope == "parts" else selection.selection.observed_parts
        )
        for index, part in enumerate(parts):
            if not part.source_sha256 or repo.get_artifact_metadata(part.source_sha256) is None:
                continue
            repo.save_selection_artifact(
                SelectionArtifactReference(
                    selection_id=selection.selection_id,
                    part_scope=scope,
                    part_index=index,
                    artifact_sha256=part.source_sha256,
                )
            )
    if origin_trace is not None and sejm_origin is not None and edition is not None:
        try:
            if isinstance(sejm_origin, SejmAdoptionOrigin):
                proved = _prove_sejm_adoption(
                    origin_trace, sejm_origin, selection, observation, edition, reads
                )
                with suppress(ValueError):
                    if proved is not None and _record_native_supersession(
                        repo, proved, sejm_origin, observation
                    ):
                        return
            else:
                proved = prove_sejm_proposal(
                    origin_trace, sejm_origin, selection, observation, edition, reads
                )
            if proved is not None:
                repo.save_candidate_disposition_trace(observation.observation_id, proved)
        except ValueError:
            log.debug("Sejm source proof failed validation")


def _record_native_supersession(
    repo: BillRepository,
    trace: CandidateDispositionTrace,
    origin: SejmAdoptionOrigin,
    current: SourceObservation,
) -> bool:
    proof, report = trace.sejm_adopted_text, origin.predecessor
    if proof is None or report is None:
        return False
    adopted = repo.get_source_observation(current.observation_id)
    if adopted is None:
        return False
    candidate: SejmSupersededDraft | None = None
    body_hashes: set[str] = set()
    for prior in repo.list_source_observations(current.term, current.number):
        if (
            prior.purpose != "analysis"
            or prior.document.kind != "committee_report"
            or prior.document.url != report.url
        ):
            continue
        record = repo.get_document_selection(prior.selection_id)
        if record is None:
            return False
        selection = record.selection
        body_hashes.update(
            part.source_sha256
            for part in (*selection.parts, *selection.observed_parts)
            if part.source_sha256
        )
        if (
            selection.question != "sejm_committee_draft"
            or selection.policy_version != "sejm-committee-draft-pages-v1"
            or prior.recorded_at >= adopted.recorded_at
        ):
            continue
        if candidate is not None or len(selection.parts) != 1:
            return False
        (part,) = selection.parts
        edition = repo.get_selection_edition(prior.selection_id)
        if (
            edition is None
            or part.page_start is None
            or part.page_end is None
            or not repo.list_selection_extractions(prior.selection_id)
        ):
            return False
        candidate = SejmSupersededDraft(
            report=report.report,
            report_date=report.report_date,
            observation_id=prior.observation_id,
            edition_id=edition.edition_id,
            pdf_sha256=part.sha256,
            text_sha256=part.text_sha256,
            first_page=part.page_start,
            last_page=part.page_end,
        )
    if candidate is None or body_hashes != {candidate.pdf_sha256}:
        return False
    linked = CandidateDispositionTrace.model_validate(
        {
            **trace.model_dump(),
            "sejm_adopted_text": {**proof.model_dump(), "supersedes": candidate},
        }
    )
    repo.save_candidate_disposition_trace(current.observation_id, linked)
    return True


def _prove_sejm_adoption(
    trace: CandidateDispositionTrace,
    origin: SejmAdoptionOrigin,
    selection: DocumentSelectionRecord,
    observation: SourceObservation,
    edition: DocumentEdition,
    reads: tuple[ArtifactReadIdentity, ...],
) -> CandidateDispositionTrace | None:
    parts, stage = selection.selection.parts, origin.stage
    if (
        (observation.term, observation.number) != (origin.term, origin.number)
        or observation.purpose != "analysis"
        or edition.member_count != 1
        or len(parts) != 1
        or stage.stage_type != "SejmReading"
        or stage.date is None
        or stage.decision is None
        or stage.text_after3 != observation.document.url
    ):
        return None
    (part,) = parts
    if (
        part.role != "bill"
        or part.members
        or part.url != observation.document.url
        or part.source_sha256 is None
        or not any(
            (read.requested_url, read.sha256) == (part.url, part.source_sha256) for read in reads
        )
    ):
        return None
    proof = SejmAdoptedText(
        term=origin.term,
        number=origin.number,
        stage_name=stage.stage_name,
        decision=stage.decision,
        stage_date=stage.date,
        pdf_sha256=part.source_sha256,
        pdf_text_sha256=part.text_sha256,
        selection_id=selection.selection_id,
        edition_id=edition.edition_id,
    )
    return CandidateDispositionTrace.model_validate(
        {**trace.model_dump(), "sejm_adopted_text": proof}
    )


def prove_sejm_proposal(
    trace: CandidateDispositionTrace,
    origin: SejmProposalOrigin,
    selection: DocumentSelectionRecord,
    observation: SourceObservation,
    edition: DocumentEdition,
    reads: tuple[ArtifactReadIdentity, ...],
) -> CandidateDispositionTrace | None:
    parts = selection.selection.parts
    if (
        (observation.term, observation.number) != (origin.term, origin.parent_print)
        or observation.purpose != "analysis"
        or len(parts) != 2
        or edition.member_count != 1
    ):
        return None
    base, proposal = parts
    if (
        (base.role, proposal.role) != ("bill", "self_amendment")
        or base.members
        or proposal.members
        or base.source_sha256 is None
        or proposal.source_sha256 is None
    ):
        return None
    actual_reads = {(read.requested_url, read.sha256) for read in reads}
    if (base.url, base.source_sha256) not in actual_reads or (
        proposal.url,
        proposal.source_sha256,
    ) not in actual_reads:
        return None
    proof = SejmOriginalProposal(
        term=origin.term,
        parent_print=origin.parent_print,
        proposal_print=origin.proposal_print,
        official_process_prints=origin.official_process_prints,
        base_sha256=base.source_sha256,
        proposal_sha256=proposal.source_sha256,
        selection_id=selection.selection_id,
        edition_id=edition.edition_id,
    )
    return CandidateDispositionTrace.model_validate(
        {**trace.model_dump(), "sejm_original_proposal": proof}
    )

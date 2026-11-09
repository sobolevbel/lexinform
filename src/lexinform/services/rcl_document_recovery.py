"""Recover RCL revisions when the recorded sources establish the missing relationship."""

import datetime as dt
import hashlib
from contextlib import suppress
from dataclasses import replace
from typing import Protocol
from urllib.parse import urlparse

from lexinform.document_candidate_observations import CandidateObservations, with_candidate_read
from lexinform.document_candidates import DocumentCandidate
from lexinform.document_composition import finalize_selection
from lexinform.document_editions import candidate_edition
from lexinform.document_selection import RCL_RESUBMISSION_POLICY_VERSION
from lexinform.errors import DownloadError
from lexinform.models import (
    ArtifactIdentity,
    ArtifactReadIdentity,
    CandidateDisposition,
    CandidateDispositionTrace,
    DocumentPart,
    DocumentSelectionRecord,
    ExtractionResult,
    TextDocument,
)
from lexinform.models.candidate_observation import CandidateObservation
from lexinform.models.candidate_trace import (
    AmendmentPartGap,
    RclOriginalProposal,
    RclProposalResubmission,
)
from lexinform.models.document_reads import LoadedFile, RclProposalRead
from lexinform.models.rcl import RclProject, rcl_num_key
from lexinform.ports import SejmGateway
from lexinform.rcl_amendments import rcl_amendment_base_index, rcl_self_amendment_part
from lexinform.rcl_resubmission import (
    resubmission_members,
    resubmission_proof,
    resubmission_sources,
)
from lexinform.services.rcl_amendment_original import read_rcl_amendment_original


class _RecoveryReads(Protocol):
    def read(self, url: str) -> LoadedFile: ...

    def read_bounded(self, url: str, *, max_bytes: int) -> LoadedFile: ...

    def read_document(self, document: TextDocument) -> LoadedFile: ...

    def file_origins(
        self, url: str, loaded: LoadedFile
    ) -> tuple[ArtifactIdentity | None, ArtifactReadIdentity | None, ExtractionResult | None]: ...


def recover_rcl_document(
    reads: _RecoveryReads,
    document: TextDocument,
    project: RclProject,
    loaded: LoadedFile,
    *,
    proposal_analysis: bool,
    sejm: SejmGateway | None,
) -> tuple[TextDocument, LoadedFile] | None:
    if proposal_analysis:
        with suppress(Exception):
            resubmitted = _recover_rcl_resubmission(reads, document, project, loaded)
            if resubmitted is not None:
                return document, resubmitted
    if loaded.selection.state in ("waiting", "unverified", "selected"):
        with suppress(ValueError):
            return _recover_rcl_amendment(reads, document, project, sejm)
    return None


def _recover_rcl_resubmission(
    reads: _RecoveryReads, document: TextDocument, project: RclProject, waiting: LoadedFile
) -> LoadedFile | None:
    source_set = CandidateObservations.from_sources(document)
    sources = resubmission_sources(project, source_set)
    if sources is None:
        return None
    trace = waiting.candidate_trace
    if (
        trace is None
        or trace.result != "waiting"
        or trace.coverage != "complete"
        or trace.requested_document != document
        or len(trace.dispositions) != 3
        or tuple(row.reason for row in trace.dispositions)
        != (
            "version_comparison_deferred",
            "normative_part_read_incomplete",
            "no_readable_body",
        )
    ):
        return None
    gaps = trace.version_part_gaps
    if len(gaps) != 1:
        return None
    (gap,) = gaps
    if (
        not isinstance(gap, AmendmentPartGap)
        or gap.index != 1
        or gap.amendment_index != 0
        or gap.reason != "returned_no_text"
        or gap.file_sha256 is None
    ):
        return None
    main = reads.read(sources.later.url)
    older = reads.read(sources.older.url)
    amendment = reads.read(sources.amendment.url)
    if (
        main.sha256 != trace.dispositions[0].observed_sha256
        or older.sha256 != trace.dispositions[1].observed_sha256
        or amendment.sha256 != gap.file_sha256
        or amendment.sha256 != trace.dispositions[2].observed_sha256
    ):
        return None
    _, main_read, main_extraction = reads.file_origins(sources.later.url, main)
    _, older_read, _ = reads.file_origins(sources.older.url, older)
    _, amendment_read, amendment_extraction = reads.file_origins(sources.amendment.url, amendment)
    if (
        main_read is None
        or older_read is None
        or amendment_read is None
        or main_extraction is None
        or amendment_extraction is None
        or amendment_extraction.rejected_count
        or amendment_extraction.physical_part_count != 2
    ):
        return None
    loaded = finalize_selection(document, reads.read_document(document))
    if (
        loaded.text is None
        or loaded.selection.state not in ("selected", "unverified")
        or "normative_text_omitted" in loaded.selection.limitations
        or not any(fact.kind == "coverage" for fact in loaded.selection.facts)
    ):
        return None
    observations = CandidateObservations(
        tuple(
            with_candidate_read(
                entry, file.text, file.selection.parts, file.selection.observed_parts
            )
            for entry, file in zip(source_set.entries, (loaded, older, amendment), strict=True)
        )
    )
    members = resubmission_members(sources, observations)
    if members is None:
        return None
    bill, cover, proposal = members
    letter = reads.read_bounded(sources.secretary.url, max_bytes=2_000_000)
    _, letter_read, _ = reads.file_origins(sources.secretary.url, letter)
    page = letter.first_pdf_page
    if letter_read is None or page is None or letter.complete_pdf_pages != 1:
        return None
    corroboration = CandidateObservation(
        3,
        DocumentCandidate(
            letter_read.requested_url,
            page.text,
            (
                DocumentPart(
                    url=letter_read.requested_url,
                    sha256=letter_read.sha256,
                    text=page.text,
                    pages=1,
                    text_sha256=hashlib.sha256(page.text.encode()).hexdigest(),
                ),
            ),
        ),
    )
    proof = resubmission_proof(
        sources,
        bill=bill,
        cover=cover,
        amendment=proposal,
        corroboration=corroboration,
    )
    earlier_date, revised_date = sources.older.created, sources.later.created
    if proof is None or earlier_date is None or revised_date is None:
        return None
    observed = tuple(
        dict.fromkeys(
            (
                *loaded.selection.observed_parts,
                proof.cover,
                proof.amendment,
                proof.secretary,
            )
        )
    )
    later_row, older_row, amendment_row = trace.dispositions
    decision = RclProposalResubmission(
        project_id=project.id,
        stage_id=project.reached_stages[-1].id,
        secretary_id=sources.secretary.id,
        secretary_sha256=letter_read.sha256,
        rm_number=sources.rm_key,
        older_date=earlier_date,
        revised_date=revised_date,
        bill_part=loaded.selection.parts.index(proof.bill),
        cover_part=observed.index(proof.cover),
        amendment_part=observed.index(proof.amendment),
        secretary_part=observed.index(proof.secretary),
        bill_member_sha256=proof.bill.sha256,
        cover_member_sha256=proof.cover.sha256,
        amendment_member_sha256=proof.amendment.sha256,
        prior_gap=gap,
    )
    selected_trace = CandidateDispositionTrace.model_validate(
        {
            "coverage": "complete",
            "requested_document": document,
            "universe_sha256": trace.universe_sha256,
            "candidate_count": trace.candidate_count,
            "omitted_dispositions": 0,
            "dispositions": (
                later_row.model_copy(
                    update={
                        "outcome": "selected",
                        "reason": "rcl_proposal_resubmitted",
                    }
                ),
                older_row.model_copy(
                    update={
                        "outcome": "policy_excluded",
                        "reason": "prior_rcl_proposal",
                    }
                ),
                amendment_row,
            ),
            "result": "selected",
            "reason": "rcl_proposal_resubmitted",
            "selection_state": loaded.selection.state,
            "rcl_proposal_resubmission": decision,
        }
    )
    return replace(
        loaded,
        selection=loaded.selection.model_copy(
            update={
                "policy_version": RCL_RESUBMISSION_POLICY_VERSION,
                "observed_parts": observed,
            }
        ),
        candidate_trace=selected_trace,
        rcl_resubmission_reads=(main_read, older_read, amendment_read, letter_read),
    )


def _recover_rcl_amendment(
    reads: _RecoveryReads,
    document: TextDocument,
    project: RclProject,
    sejm: SejmGateway | None,
) -> tuple[TextDocument, LoadedFile] | None:
    if (
        sejm is None
        or project.sejm_term is None
        or not rcl_num_key(project.rm_number or "")
        or document.kind != "rcl"
        or not document.compare_versions
        or document.official_link_conflict
        or len(document.version_basis) != 2
        or not document.version_basis[0].startswith(f"rcl/{project.id}/stage/")
        or document.required_urls
        or document.normative_urls
        or document.extra_urls
        or len(document.amendment_urls) != 1
    ):
        return None
    (url,) = document.amendment_urls
    if (
        not urlparse(url).path.casefold().endswith(".zip")
        or len(document.alternatives) > 1
        or any(item.url != url or item.official_link_conflict for item in document.alternatives)
    ):
        return None
    try:
        archive = reads.read(url)
        base = reads.read(document.url)
        _, _, archive_extraction = reads.file_origins(url, archive)
        if (
            archive.selection.state != "unsupported"
            or archive_extraction is None
            or archive_extraction.rejected_count
            or archive_extraction.physical_part_count != len(archive.selection.observed_parts)
            or base.archive_unpack_blocked
        ):
            return None
        part = rcl_self_amendment_part(
            archive.selection.observed_parts,
            project_title=project.title,
            wykaz_number=project.wykaz_number,
        )
        if part is None or base.sha256 is None or archive.sha256 is None:
            return None
        original = read_rcl_amendment_original(sejm, reads.read, project=project, proposal=part)
        if original is None:
            return None
        process, word = original
        if (
            rcl_amendment_base_index(
                part,
                project_rm_number=project.rm_number,
                print_process=process,
                original=word.selection,
                candidates=(base.selection,),
            )
            != 0
        ):
            return None
        _, read, extraction = reads.file_origins(word.selection.parts[0].url, word)
        if read is None or extraction is None:
            return None
    except DownloadError, ValueError, OSError:
        return None
    if base.text is None:
        return None
    selection = base.selection.model_copy(
        update={
            "policy_version": "document-selection-v21",
            "version": document.version_basis,
            "parts": (
                *base.selection.parts,
                part.model_copy(update={"role": "self_amendment"}),
            ),
        }
    )
    decisions = [
        CandidateDisposition(
            phase="version_comparison",
            index=0,
            outcome="selected",
            reason="version_choice",
            observed_sha256=base.sha256,
        )
    ]
    if document.alternatives:
        decisions.append(
            CandidateDisposition(
                phase="version_comparison",
                index=1,
                outcome="non_bill" if archive.text is not None else "unclassified",
                reason="non_bill_body" if archive.text is not None else "no_readable_body",
                observed_sha256=archive.sha256,
            )
        )
    trace = CandidateDispositionTrace.from_decisions(
        document,
        tuple(decisions),
        result="selected",
        reason="version_choice",
        selection_state=selection.state,
    )
    record = DocumentSelectionRecord.from_selection(selection)
    edition = candidate_edition(record, recorded_at=dt.datetime(1970, 1, 1, tzinfo=dt.UTC))
    bill_part = next((p for p in selection.parts if p.role == "bill"), None)
    if edition is None or bill_part is None or not bill_part.members:
        return None
    proof = RclOriginalProposal(
        index=0,
        amendment_index=0,
        project_id=project.id,
        rm_number=rcl_num_key(project.rm_number or ""),
        term=process.term,
        original_print=process.number,
        original_extraction_id=extraction.result_id,
        base_sha256=base.sha256,
        base_member_sha256=bill_part.sha256,
        proposal_sha256=archive.sha256,
        proposal_member_sha256=part.sha256,
        comparison_text_sha256=hashlib.sha256(bill_part.text.strip().encode()).hexdigest(),
        edition_id=edition.edition_id,
    )
    enriched = CandidateDispositionTrace.model_validate(
        {**trace.model_dump(), "rcl_original_proposal": proof}
    )
    return document, replace(
        base,
        selection=selection,
        candidate_trace=trace,
        rcl_proposal=RclProposalRead(enriched, read, extraction, base.base_bill_sample),
    )

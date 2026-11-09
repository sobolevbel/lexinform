"""Join prospective text receipts to parents from the actual captured read only."""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import cast

from lexinform.document_editions import candidate_edition
from lexinform.input_receipts import (
    amendment_input_receipt,
    archive_member_matches,
    ordinary_rcl_archive_document,
    primary_input_receipt,
    rcl_archive_self_amendment_url,
    selected_archive_version_peer,
    supplement_input_receipt,
)
from lexinform.models import (
    AmendmentInputReceipt,
    AmendmentsContext,
    AmendmentsQuestion,
    AnalysisQuestion,
    ArtifactIdentity,
    BatchIntent,
    BatchItemMeta,
    BatchRequest,
    DigestItemMeta,
    DocumentPart,
    DocumentSelection,
    DocumentSelectionRecord,
    ExtractionResult,
    PrimaryInputReceipt,
    SourceObservation,
    SourceObservationIdentity,
    SupplementInputReceipt,
    SupplementQuestion,
    TextDocument,
)
from lexinform.models.block_input import BlockCopyWitness, PreparedBlockCompanion, PreparedBlockCopy
from lexinform.models.documents import SenateAmendmentInputWitness
from lexinform.models.input_receipts import (
    MAX_INPUT_RECEIPT_SEGMENTS,
    ArchiveInputPart,
    LooseRclInputPart,
    LooseRclInputReceipt,
    MultipartTextInputReceipt,
    PairedArchiveBlockCompanion,
    RclArchiveInputReceipt,
    RclArchiveSelfAmendmentInputReceipt,
    RclPairedArchiveInputReceipt,
    SejmSelfAmendmentInputReceipt,
    amendment_context_sha256,
    input_map_sha256,
    input_text_sha256,
    primary_context_sha256,
)
from lexinform.models.physical_inventory import PhysicalCapture, PhysicalRootOrder
from lexinform.models.prepared_input import PreparedTextPart, PreparedTextSegment
from lexinform.models.sejm import SejmProposalOrigin
from lexinform.physical_observations import OrderedPhysicalObservation
from lexinform.physical_root_assembly import PhysicalAssemblyPacket
from lexinform.ports import BillRepository
from lexinform.prepared_input import prepared_text_input
from lexinform.services.document_history import (
    DocumentSourceRead,
    SourceReadCapture,
    prove_sejm_proposal,
)
from lexinform.text_projection import TextProjection


def _body(part: DocumentPart, source: SourceReadCapture | None) -> ArtifactIdentity | None:
    if source is None or not part.sha256:
        return None
    matched = None
    for candidate in source.artifacts:
        if candidate.sha256 != part.sha256:
            continue
        if matched is not None and matched.byte_size != candidate.byte_size:
            return None
        matched = candidate
    return matched


def _extraction(
    part: DocumentPart, body: ArtifactIdentity | None, source: SourceReadCapture | None
) -> ExtractionResult | None:
    if source is None or body is None or not part.text_sha256:
        return None
    matched = None
    for candidate in source.extractions:
        if (
            candidate.artifact_sha256 != body.sha256
            or candidate.output_text_sha256 != part.text_sha256
            or candidate.output_chars != len(part.text)
            or candidate.physical_part_count != 1
            or candidate.selected_part_count != 1
            or candidate.omitted_selected_parts != 0
            or len(candidate.selected_parts) != 1
        ):
            continue
        summary = candidate.selected_parts[0]
        if (
            summary.physical_index != 0
            or summary.sha256 != body.sha256
            or summary.text_sha256 != part.text_sha256
            or summary.text_chars != len(part.text)
        ):
            continue
        if matched is not None and matched != candidate:
            return None
        matched = candidate
    return matched


def capture_primary_input_receipt(
    request: BatchRequest,
    selection: DocumentSelection,
    projection: TextProjection | None,
    source: SourceReadCapture | None,
) -> PrimaryInputReceipt | None:
    if (
        not isinstance(request.question, AnalysisQuestion)
        or request.call_kind not in ("analysis", "reanalysis")
        or request.question.ctx.scan is not None
        or request.question.ctx.text_source == "scan"
        or selection.scan_input is not None
        or len(selection.parts) != 1
        or selection.parts[0].members
        or any(span.stage == "analysis" for span in selection.coverage)
    ):
        return None
    part = selection.parts[0]
    body = _body(part, source)
    return primary_input_receipt(
        request,
        selection,
        projection=projection,
        known_selection=source.selection if isinstance(source, DocumentSourceRead) else None,
        body=body,
        extraction=_extraction(part, body, source),
    )


@dataclass(frozen=True)
class PairedArchiveParent:
    packet: PhysicalAssemblyPacket
    physical_index: int
    ordinal: int


def paired_archive_parent(
    source: DocumentSourceRead, observations: tuple[OrderedPhysicalObservation, ...]
) -> PairedArchiveParent | None:
    proof = source.rcl_proposal.trace.rcl_original_proposal if source.rcl_proposal else None
    if proof is None or len(source.selection.selection.parts) < 2:
        return None
    amendment = source.selection.selection.parts[-1]
    if (
        amendment.role != "self_amendment"
        or not amendment.members
        or (amendment.source_sha256, amendment.sha256)
        != (proof.proposal_sha256, proof.proposal_member_sha256)
        or proof.base_sha256 == proof.proposal_sha256
    ):
        return None
    return _exact_archive_parent(
        amendment, {item.result_id for item in source.extractions}, observations
    )


def _exact_archive_parent(
    part: DocumentPart,
    extraction_ids: set[str],
    observations: tuple[OrderedPhysicalObservation, ...],
) -> PairedArchiveParent | None:
    parents: dict[tuple[str, int, int], PairedArchiveParent] = {}
    for entry in observations:
        packet = entry.observation.assembly
        if (
            not isinstance(packet, PhysicalAssemblyPacket)
            or packet.capture.artifact_sha256 != part.source_sha256
            or packet.capture.extraction_id not in extraction_ids
        ):
            continue
        metadata = {item.metadata_id: item for item in packet.metadata}
        execution = metadata.get(packet.capture.execution_id)
        if (
            execution is None
            or execution.coverage != "complete"
            or not isinstance(order := execution.metadata, PhysicalRootOrder)
        ):
            continue
        for slot, (owner, text_sha256, _) in enumerate(order.physical):
            if owner >= len(order.contexts):
                continue
            parent, ordinal, physical_slot, unit_id = order.contexts[owner]
            unit = metadata.get(unit_id)
            if (
                parent == 0
                and ordinal is not None
                and physical_slot == slot
                and unit is not None
                and unit.body_sha256 == part.sha256
                and text_sha256 == part.text_sha256
            ):
                parents[(packet.capture.capture_id, slot, ordinal)] = PairedArchiveParent(
                    packet, slot, ordinal
                )
    return next(iter(parents.values())) if len(parents) == 1 else None


def paired_fitted_base(
    source: DocumentSourceRead, observations: tuple[OrderedPhysicalObservation, ...]
) -> PhysicalAssemblyPacket | None:
    proposal = source.rcl_proposal
    proof = proposal.trace.rcl_original_proposal if proposal is not None else None
    bill = source.selection.selection.parts[0]
    if proof is None or (bill.role, bill.source_sha256, bill.sha256) != (
        "bill",
        proof.base_sha256,
        proof.base_member_sha256,
    ):
        return None
    parent = _exact_archive_parent(
        bill, {item.result_id for item in source.extractions}, observations
    )
    return parent.packet if parent is not None else None


def capture_rcl_archive_input_receipt(
    request: BatchRequest,
    selection: DocumentSelection,
    projection: TextProjection | None,
    source: SourceReadCapture | None,
    *,
    paired: PairedArchiveParent | None = None,
    fitted_base: PhysicalAssemblyPacket | None = None,
) -> (
    RclArchiveInputReceipt
    | RclArchiveSelfAmendmentInputReceipt
    | RclPairedArchiveInputReceipt
    | None
):
    if (
        not isinstance(request.question, AnalysisQuestion)
        or request.call_kind not in ("analysis", "reanalysis")
        or request.question.ctx.source_kind != "rcl"
        or request.question.ctx.scan is not None
        or request.question.ctx.text_source == "scan"
        or request.payload_sha256 is None
        or projection is None
        or len(projection.segments) > MAX_INPUT_RECEIPT_SEGMENTS
        or not isinstance(source, DocumentSourceRead)
        or not 1 <= len(selection.parts) <= 6
        or not selection.parts[0].members
        or selection.scan_input is not None
        or any(span.stage == "analysis" for span in selection.coverage)
    ):
        return None
    ctx = request.question.ctx
    if projection.text != ctx.text:
        return None
    archive = selection.parts
    amendment: ArchiveInputPart | LooseRclInputPart | None = None
    trace = source.candidate_trace
    version_choice = (
        source.document.compare_versions
        and len(source.document.alternatives) == 1
        and trace is not None
        and trace.version_decision is not None
        and not source.document.amendment_urls
    )
    if paired is not None:
        if len(archive) < 2 or source.rcl_proposal is None:
            return None
        part = archive[-1]
        if (
            part.role != "self_amendment"
            or part.source_sha256 != paired.packet.capture.artifact_sha256
            or not part.members
            or (part.text_start, part.text_end) != (0, len(part.text))
        ):
            return None
        amendment = ArchiveInputPart(
            role=part.role,
            chars=len(part.text),
            text_sha256=part.text_sha256,
            physical_index=paired.physical_index,
        )
        archive = archive[:-1]
    elif not version_choice and not ordinary_rcl_archive_document(source.document):
        amendment_url = rcl_archive_self_amendment_url(source.document)
        if amendment_url is None or len(archive) < 3:
            return None
        loose = archive[-1]
        if (
            loose.url != amendment_url
            or loose.role != "self_amendment"
            or loose.members
            or loose.text_start != 0
            or loose.text_end != len(loose.text)
            or loose.sha256 == archive[0].source_sha256
        ):
            return None
        body = _body(loose, source)
        extraction = _extraction(loose, body, source)
        if (
            body is None
            or extraction is None
            or extraction.selected_parts[0].format != "pdf"
            or {
                (read.sha256, read.byte_size)
                for read in source.reads
                if read.requested_url == amendment_url
            }
            != {(body.sha256, body.byte_size)}
        ):
            return None
        amendment = LooseRclInputPart(
            role=loose.role,
            chars=len(loose.text),
            text_sha256=loose.text_sha256,
            extraction_id=extraction.result_id,
        )
        archive = archive[:-1]
    root = archive[0].source_sha256
    reads = {
        (read.sha256, read.byte_size)
        for read in source.reads
        if read.requested_url == source.document.url
    }
    if len(reads) != 1:
        return None
    ((read_sha, size),) = reads
    if read_sha != root or not any(
        (body.sha256, body.byte_size) == (root, size) for body in source.artifacts
    ):
        return None
    extractions = tuple(
        item
        for item in source.extractions
        if item.artifact_sha256 == root
        and item.selected_part_count == len(archive)
        and item.omitted_selected_parts == 0
    )
    if len(extractions) != 1:
        return None
    (extraction,) = extractions
    parts: list[ArchiveInputPart] = []
    for part in archive:
        chars = len(part.text)
        if (
            not part.members
            or (part.url, part.source_sha256) != (source.document.url, root)
            or (part.text_start, part.text_end) != (0, chars)
        ):
            return None
        matched = tuple(
            summary
            for summary in extraction.selected_parts
            if archive_member_matches(part, summary, chars=chars)
        )
        if len(matched) != 1:
            return None
        parts.append(
            ArchiveInputPart(
                role=part.role,
                chars=chars,
                text_sha256=part.text_sha256,
                physical_index=matched[0].physical_index,
            )
        )
    request.verify_payload()
    prepared = prepared_text_input(selection, ctx.text, projection=projection)
    if prepared.source_selection_id != source.selection.selection_id:
        return None
    fields = {
        "source_selection_id": prepared.source_selection_id,
        "source_observation_id": SourceObservationIdentity(
            term=request.term,
            number=request.number,
            purpose="analysis",
            document=source.document,
            selection_id=prepared.source_selection_id,
        ).observation_id,
        "extraction_id": extraction.result_id,
        "context_sha256": primary_context_sha256(ctx),
        "request_sha256": request.payload_sha256,
        "text_sha256": prepared.text_sha256,
        "text_chars": prepared.text_chars,
        "full_map_sha256": input_map_sha256(prepared.segments),
        "segments": prepared.segments,
    }
    if amendment is None:
        if version_choice and trace is not None:
            peer_sha = selected_archive_version_peer(
                source.document, selection, trace, root, parts[0].physical_index
            )
            if peer_sha is None or not any(
                (read.requested_url, read.sha256) == (source.document.alternatives[0].url, peer_sha)
                for read in source.reads
            ):
                return None
            fields["selected_version_trace_id"] = trace.trace_id
        return RclArchiveInputReceipt.model_validate({**fields, "source_parts": tuple(parts)})
    final = prepared.segments[-1]
    complete_amendment = (final.part_index, final.source_start, final.source_end) == (
        len(archive),
        0,
        amendment.chars,
    ) and input_text_sha256(ctx.text[final.start : final.end]) == amendment.text_sha256
    if not complete_amendment:
        if (
            paired is None
            or fitted_base is None
            or not ctx.truncated
            or fitted_base.capture.extraction_id != extraction.result_id
        ):
            return None
        fields["fitted_base_capture_id"] = fitted_base.capture.capture_id
    if paired is not None and source.rcl_proposal is not None:
        return RclPairedArchiveInputReceipt.model_validate(
            {
                **fields,
                "source_parts": (*parts, amendment),
                "proposal_trace_id": source.rcl_proposal.trace.trace_id,
                "amendment_capture_id": paired.packet.capture.capture_id,
                "amendment_ordinal": paired.ordinal,
            }
        )
    return RclArchiveSelfAmendmentInputReceipt.model_validate(
        {**fields, "source_parts": (*parts, amendment)}
    )


def capture_loose_rcl_input_receipt(
    request: BatchRequest,
    selection: DocumentSelection,
    projection: TextProjection | None,
    source: SourceReadCapture | None,
    *,
    with_amendments: bool = False,
) -> LooseRclInputReceipt | None:
    if (
        not isinstance(request.question, AnalysisQuestion)
        or request.call_kind not in ("analysis", "reanalysis")
        or request.question.ctx.source_kind != "rcl"
        or request.question.ctx.scan is not None
        or request.question.ctx.text_source == "scan"
        or request.payload_sha256 is None
        or projection is None
        or len(projection.segments) > MAX_INPUT_RECEIPT_SEGMENTS
        or not isinstance(source, DocumentSourceRead)
        or source.document.kind != "rcl"
        or not 2 <= len(selection.parts) <= (6 if with_amendments else 5)
        or source.document.url != selection.parts[0].url
        or selection.scan_input is not None
        or any(span.stage == "analysis" for span in selection.coverage)
    ):
        return None
    ctx = request.question.ctx
    if projection.text != ctx.text:
        return None
    parts = _loose_parts(selection, source)
    if parts is None:
        return None
    request.verify_payload()
    prepared = prepared_text_input(selection, ctx.text, projection=projection)
    if (
        prepared.source_selection_id != source.selection.selection_id
        or len(prepared.segments) > MAX_INPUT_RECEIPT_SEGMENTS
    ):
        return None
    return LooseRclInputReceipt(
        profile=(
            "primary-loose-rcl-mixed-text-v1"
            if with_amendments
            else "primary-loose-rcl-annex-text-v1"
        ),
        source_selection_id=prepared.source_selection_id,
        source_observation_id=SourceObservationIdentity(
            term=request.term,
            number=request.number,
            purpose="analysis",
            document=source.document,
            selection_id=source.selection.selection_id,
        ).observation_id,
        source_parts=parts,
        context_sha256=primary_context_sha256(ctx),
        request_sha256=request.payload_sha256,
        text_sha256=prepared.text_sha256,
        text_chars=prepared.text_chars,
        full_map_sha256=input_map_sha256(prepared.segments),
        segments=prepared.segments,
    )


def _loose_parts(
    selection: DocumentSelection, source: DocumentSourceRead
) -> tuple[LooseRclInputPart, ...] | None:
    parts: list[LooseRclInputPart] = []
    for part in selection.parts:
        if (
            part.members
            or not part.text_sha256
            or part.text_start != 0
            or part.text_end != len(part.text)
        ):
            return None
        body = _body(part, source)
        extraction = _extraction(part, body, source)
        if extraction is None:
            return None
        parts.append(
            LooseRclInputPart(
                role=part.role,
                chars=len(part.text),
                text_sha256=part.text_sha256,
                extraction_id=extraction.result_id,
            )
        )
    return tuple(parts)


def capture_sejm_self_amendment_input_receipt(
    request: BatchRequest,
    selection: DocumentSelection,
    projection: TextProjection | None,
    source: DocumentSourceRead,
    *,
    recorded_at: datetime,
) -> SejmSelfAmendmentInputReceipt | None:
    origin = source.sejm_origin
    trace = source.candidate_trace
    if (
        not isinstance(origin, SejmProposalOrigin)
        or trace is None
        or not trace.is_routine_singleton
        or trace.archive_members is not None
        or not isinstance(request.question, AnalysisQuestion)
        or request.call_kind not in ("analysis", "reanalysis")
        or request.payload_sha256 is None
        or projection is None
        or selection.scan_input is not None
        or any(span.stage == "analysis" for span in selection.coverage)
        or tuple(part.role for part in selection.parts) != ("bill", "self_amendment")
    ):
        return None
    ctx = request.question.ctx
    if ctx.source_kind != "print" or ctx.scan is not None or projection.text != ctx.text:
        return None
    edition = candidate_edition(source.selection, recorded_at=recorded_at)
    if edition is None:
        return None
    observation = SourceObservation(
        term=request.term,
        number=request.number,
        purpose="analysis",
        document=source.document,
        selection_id=source.selection.selection_id,
        recorded_at=recorded_at,
    )
    proof = prove_sejm_proposal(trace, origin, source.selection, observation, edition, source.reads)
    if proof is None:
        return None
    parts = _loose_parts(selection, source)
    if parts is None:
        return None
    request.verify_payload()
    prepared = prepared_text_input(selection, ctx.text, projection=projection)
    if (
        prepared.source_selection_id != observation.selection_id
        or len(prepared.segments) > MAX_INPUT_RECEIPT_SEGMENTS
    ):
        return None
    return SejmSelfAmendmentInputReceipt(
        source_selection_id=prepared.source_selection_id,
        source_observation_id=observation.observation_id,
        proposal_trace_id=proof.trace_id,
        source_parts=parts,
        context_sha256=primary_context_sha256(ctx),
        request_sha256=request.payload_sha256,
        text_sha256=prepared.text_sha256,
        text_chars=prepared.text_chars,
        full_map_sha256=input_map_sha256(prepared.segments),
        segments=prepared.segments,
    )


def enrich_primary_input_intent(
    original: BatchIntent,
    receipt: PrimaryInputReceipt | MultipartTextInputReceipt,
) -> BatchIntent:
    if not isinstance(original.meta, BatchItemMeta):
        raise ValueError("Primary receipt enrichment requires primary metadata")
    return _enrich_input_intent(original, receipt)


def _enrich_input_intent(
    original: BatchIntent,
    receipt: PrimaryInputReceipt
    | MultipartTextInputReceipt
    | SupplementInputReceipt
    | AmendmentInputReceipt,
) -> BatchIntent:
    return BatchIntent.model_validate(
        {
            "request": original.request,
            "meta": original.meta.model_copy(update={"prepared_input_receipt": receipt}),
            "provider": original.provider,
            "state": original.state,
            "created_at": original.created_at,
        }
    )


def capture_secondary_input_receipt(
    request: BatchRequest,
    selection: DocumentSelection,
    projection: TextProjection | None,
    source: SourceReadCapture | None,
) -> SupplementInputReceipt | AmendmentInputReceipt | None:
    if (
        not isinstance(request.question, (SupplementQuestion, AmendmentsQuestion))
        or request.question.ctx.scan is not None
        or selection.scan_input is not None
        or not isinstance(source, DocumentSourceRead)
        or projection is None
        or len(selection.parts) != 1
        or selection.parts[0].members
        or any(span.stage == "analysis" for span in selection.coverage)
    ):
        return None
    part = selection.parts[0]
    body = _body(part, source)
    extraction = _extraction(part, body, source)
    if body is None or extraction is None:
        return None
    builder = (
        amendment_input_receipt
        if isinstance(request.question, AmendmentsQuestion)
        else supplement_input_receipt
    )
    captured = builder(
        request,
        selection,
        projection=projection,
        known_selection=source.selection,
        body=body,
        extraction=extraction,
    )
    return (
        with_block_companion(captured, source, request.question.ctx.text, selection=selection)
        if captured
        else None
    )


def _senate_work_parents(
    repo: BillRepository,
    witness: SenateAmendmentInputWitness,
    context: AmendmentsContext,
    document: TextDocument,
    *,
    term: int,
) -> tuple[SourceObservation, DocumentPart, ExtractionResult] | None:
    if (
        context.source_kind != "senate_amendments"
        or context.scan is not None
        or context.source_check is not None
        or context.truncated
        or not context.text.strip()
        or document.kind != "senate_amendments"
        or document.senate is None
        or document.senate.scan
        or witness.context_sha256 != amendment_context_sha256(context)
        or witness.text_sha256 != input_text_sha256(context.text)
    ):
        return None
    observation = repo.get_source_observation(witness.source_observation_id)
    if observation is None or (
        observation.observation_id,
        observation.term,
        observation.number,
        observation.purpose,
        observation.document,
    ) != (witness.source_observation_id, term, context.number, "analysis", document):
        return None
    selection = repo.get_document_selection(observation.selection_id)
    extraction = repo.get_extraction_result(witness.extraction_id)
    if (
        selection is None
        or selection.selection_id != observation.selection_id
        or extraction is None
        or extraction.result_id != witness.extraction_id
        or (selection.selection.question, selection.selection.policy_version)
        != ("senate_resolution_source", "senate-source-observation-v1")
        or selection.selection.scan_input is not None
        or len(selection.selection.parts) != 1
        or extraction.artifact_sha256 != document.senate.sha256
        or witness.source_start + len(context.text) != extraction.output_chars
    ):
        return None
    (part,) = selection.selection.parts
    if (
        (part.url, part.role, part.sha256, part.text_sha256)
        != (document.url, "unknown", extraction.artifact_sha256, extraction.output_text_sha256)
        or part.members
        or part.text_start != 0
        or part.text_end not in (None, extraction.output_chars)
        or witness.source_start == 0
        and witness.text_sha256 != extraction.output_text_sha256
        or repo.get_artifact_metadata(extraction.artifact_sha256) is None
        or not any(
            link.part_scope == "parts"
            and link.part_index == 0
            and link.artifact_sha256 == extraction.artifact_sha256
            for link in repo.list_selection_artifacts(observation.selection_id)
        )
        or not any(
            link.extraction_id == witness.extraction_id
            and link.artifact_sha256 == extraction.artifact_sha256
            for link in repo.list_selection_extractions(observation.selection_id)
        )
    ):
        return None
    return observation, part, extraction


def capture_senate_work_input_witness(
    repo: BillRepository,
    context: AmendmentsContext,
    document: TextDocument,
    selection: DocumentSelection,
    projection: TextProjection | None,
    source: SourceReadCapture | None,
    *,
    term: int,
) -> SenateAmendmentInputWitness | None:
    if (
        not isinstance(source, DocumentSourceRead)
        or source.document != document
        or projection is None
        or projection.text != context.text
        or len(projection.segments) != 1
        or len(selection.parts) != 1
        or DocumentSelectionRecord.from_selection(selection) != source.selection
    ):
        return None
    (part,) = selection.parts
    start = projection.segments[0].source_start
    if (
        start is None
        or projection.source_sha256 != part.text_sha256
        or projection.source_length != len(part.text)
        or part.text[start:] != context.text
    ):
        return None
    extraction = _extraction(part, _body(part, source), source)
    if extraction is None:
        return None
    identity = SourceObservationIdentity(
        term=term,
        number=context.number,
        purpose="analysis",
        document=source.document,
        selection_id=source.selection.selection_id,
    )
    witness = SenateAmendmentInputWitness(
        source_observation_id=identity.observation_id,
        extraction_id=extraction.result_id,
        context_sha256=amendment_context_sha256(context),
        text_sha256=input_text_sha256(context.text),
        source_start=start,
    )
    return witness if _senate_work_parents(repo, witness, context, document, term=term) else None


def senate_work_input_receipt(
    repo: BillRepository,
    request: BatchRequest,
    document: TextDocument,
    witness: SenateAmendmentInputWitness,
) -> AmendmentInputReceipt | None:
    if (
        not isinstance(request.question, AmendmentsQuestion)
        or request.payload_sha256 is None
        or request.question.ctx.number != request.number
    ):
        return None
    request.verify_payload()
    context = request.question.ctx
    parents = _senate_work_parents(repo, witness, context, document, term=request.term)
    if parents is None:
        return None
    observation, part, extraction = parents
    segments = (
        PreparedTextSegment(
            start=0,
            end=len(context.text),
            origin="source",
            part_index=0,
            source_start=witness.source_start,
            source_end=extraction.output_chars,
        ),
    )
    return AmendmentInputReceipt(
        context_sha256=witness.context_sha256,
        request_sha256=request.payload_sha256,
        text_sha256=witness.text_sha256,
        text_chars=len(context.text),
        source_part=PreparedTextPart(
            role=part.role, text_sha256=part.text_sha256, chars=extraction.output_chars
        ),
        source_selection_id=observation.selection_id,
        artifact_sha256=extraction.artifact_sha256,
        extraction_id=extraction.result_id,
        retention="complete",
        segment_count=1,
        full_map_sha256=input_map_sha256(segments),
        segments=segments,
    )


def capture_supplement_input_receipt(
    request: BatchRequest,
    selection: DocumentSelection,
    projection: TextProjection | None,
    source: SourceReadCapture | None,
) -> SupplementInputReceipt | None:
    if not isinstance(request.question, SupplementQuestion):
        return None
    receipt = capture_secondary_input_receipt(request, selection, projection, source)
    return receipt if isinstance(receipt, SupplementInputReceipt) else None


def enrich_secondary_input_intent(
    original: BatchIntent, receipt: SupplementInputReceipt | AmendmentInputReceipt
) -> BatchIntent:
    if not isinstance(original.meta, DigestItemMeta):
        raise ValueError("Secondary receipt enrichment requires secondary metadata")
    return _enrich_input_intent(original, receipt)


def enrich_supplement_input_intent(
    original: BatchIntent, receipt: SupplementInputReceipt
) -> BatchIntent:
    if not isinstance(original.meta, DigestItemMeta):
        raise ValueError("Supplement receipt enrichment requires secondary metadata")
    return _enrich_input_intent(original, receipt)


@dataclass(frozen=True)
class PairedNormativeBase:
    extraction: ExtractionResult
    packet: PhysicalAssemblyPacket


def paired_normative_base(
    receipt: RclPairedArchiveInputReceipt,
    source: DocumentSourceRead,
    observations: tuple[OrderedPhysicalObservation, ...],
) -> PairedNormativeBase | None:
    proposal = source.rcl_proposal
    if proposal is None or proposal.base_bill_sample is None:
        return None
    proof = proposal.trace.rcl_original_proposal
    sample = proposal.base_bill_sample
    bills = [part for part in source.selection.selection.parts if part.role == "bill"]
    extraction = next(
        (item for item in source.extractions if item.result_id == receipt.extraction_id), None
    )
    if proof is None or len(bills) != 1 or extraction is None:
        return None
    (bill,) = bills
    if (bill.source_sha256, bill.sha256) != (proof.base_sha256, proof.base_member_sha256):
        return None
    exact = _exact_archive_parent(bill, {extraction.result_id}, observations)
    if exact is None or exact.physical_index != sample.physical_index:
        return None
    try:
        enriched = ExtractionResult.model_validate(
            {**extraction.model_dump(), "selected_bill_block_sample": sample}
        )
    except ValueError:
        return None
    capture = exact.packet.capture
    bound = PhysicalCapture.of(
        artifact_sha256=capture.artifact_sha256,
        recipe_id=capture.recipe_id,
        inventory_id=capture.inventory_id,
        execution_id=capture.execution_id,
        extraction_id=enriched.result_id,
    )
    return PairedNormativeBase(enriched, replace(exact.packet, capture=bound))


def with_paired_archive_block_companion(
    receipt: RclPairedArchiveInputReceipt,
    parent: PairedArchiveParent,
    source: DocumentSourceRead,
    text: str,
    *,
    normative: PairedNormativeBase | None = None,
) -> RclPairedArchiveInputReceipt:
    extractions = {item.result_id: item for item in source.extractions}
    base = extractions.get(receipt.extraction_id) if normative is None else normative.extraction
    amendment_id = parent.packet.capture.extraction_id
    amendment = extractions.get(amendment_id) if amendment_id is not None else None
    if base is None or amendment is None or amendment.observed_member_block_copy is None:
        return receipt
    observed = amendment.observed_member_block_copy
    index = len(receipt.source_parts) - 1
    if observed.physical_index != receipt.amendment_part.physical_index:
        return receipt
    if normative is None:
        by_slot = {item.physical_index: item.block_copy_witness for item in base.selected_parts}
    else:
        sample = base.selected_bill_block_sample
        if sample is None:
            return receipt
        by_slot = {sample.physical_index: sample.witness}
    base_rows = [
        row
        for part_index, part in enumerate(receipt.source_parts[:-1])
        if (witness := by_slot.get(part.physical_index)) is not None
        for row in _paired_whole_block_rows(
            receipt, witness, part_index, base.result_id, part.physical_index, text
        )
    ]
    amendment_rows = _paired_whole_block_rows(
        receipt, observed.witness, index, amendment.result_id, observed.physical_index, text
    )
    if not base_rows or not amendment_rows:
        return receipt
    base_rows.sort(key=lambda row: row.start)
    base_sha256 = _block_rows_sha256(base_rows)
    amendment_sha256 = _block_rows_sha256(amendment_rows)
    for length in range(min(32, max(len(base_rows), len(amendment_rows))), 0, -1):
        try:
            companion = PairedArchiveBlockCompanion(
                base_bill_capture_id=normative.packet.capture.capture_id
                if normative is not None
                else None,
                base=_block_prefix(base_rows, min(length, len(base_rows)), base_sha256),
                amendment=_block_prefix(
                    amendment_rows, min(length, len(amendment_rows)), amendment_sha256
                ),
            )
            return RclPairedArchiveInputReceipt.model_validate(
                {
                    **receipt.model_dump(),
                    "paired_block_companion": companion,
                    "extraction_id": base.result_id,
                }
            )
        except ValueError:
            continue
    return receipt


def _paired_whole_block_rows(
    receipt: RclPairedArchiveInputReceipt,
    witness: BlockCopyWitness,
    part_index: int,
    extraction_id: str,
    physical_index: int,
    text: str,
) -> list[PreparedBlockCopy]:
    rows = []
    for segment in receipt.segments:
        if (
            segment.part_index != part_index
            or segment.source_start is None
            or segment.source_end is None
        ):
            continue
        for run_index, run in enumerate(witness.runs):
            if not segment.source_start <= run.source_start < run.source_end <= segment.source_end:
                continue
            start = segment.start + run.source_start - segment.source_start
            end = segment.start + run.source_end - segment.source_start
            if input_text_sha256(text[start:end]) == run.text_sha256:
                rows.append(
                    PreparedBlockCopy(
                        start=start,
                        end=end,
                        part_index=part_index,
                        extraction_id=extraction_id,
                        physical_index=physical_index,
                        witness_id=witness.witness_id,
                        run_index=run_index,
                        text_sha256=run.text_sha256,
                    )
                )
    return rows


def _block_rows_sha256(rows: list[PreparedBlockCopy]) -> str:
    return input_text_sha256("[" + ",".join(row.model_dump_json() for row in rows) + "]")


def _block_prefix(
    rows: list[PreparedBlockCopy], length: int, rows_sha256: str
) -> PreparedBlockCompanion:
    return PreparedBlockCompanion(
        retention="complete" if length == len(rows) else "prefix",
        observed_rows=len(rows),
        rows_sha256=rows_sha256,
        rows=tuple(rows[:length]),
    )


def with_block_companion[
    T: PrimaryInputReceipt
    | MultipartTextInputReceipt
    | SupplementInputReceipt
    | AmendmentInputReceipt
](
    receipt: T,
    source: SourceReadCapture | None,
    text: str,
    *,
    selection: DocumentSelection | None = None,
) -> T:
    try:
        return _with_block_companion(receipt, source, text, selection)
    except Exception:
        return receipt


def _with_block_companion[
    T: PrimaryInputReceipt
    | MultipartTextInputReceipt
    | SupplementInputReceipt
    | AmendmentInputReceipt
](
    receipt: T, source: SourceReadCapture | None, text: str, selection: DocumentSelection | None
) -> T:
    if not isinstance(source, DocumentSourceRead) or receipt.block_companion is not None:
        return receipt
    rows: list[PreparedBlockCopy] = []
    for segment in receipt.segments:
        if segment.origin != "source" or segment.part_index is None or segment.source_start is None:
            continue
        index = segment.part_index
        part = source.selection.selection.parts[index]
        if isinstance(receipt, MultipartTextInputReceipt):
            recorded = receipt.source_parts[index]
            expected = (
                receipt.extraction_id
                if isinstance(
                    receipt, (RclArchiveInputReceipt, RclArchiveSelfAmendmentInputReceipt)
                )
                and isinstance(recorded, ArchiveInputPart)
                else recorded.extraction_id
                if isinstance(recorded, LooseRclInputPart)
                else None
            )
        else:
            expected = receipt.extraction_id
        matches = [
            (extraction, summary)
            for extraction in source.extractions
            if extraction.result_id == expected
            for summary in extraction.selected_parts
            if archive_member_matches(part, summary, chars=summary.text_chars)
            and summary.block_copy_witness is not None
        ]
        if len(matches) != 1:
            continue
        extraction, summary = matches[0]
        witness = summary.block_copy_witness
        assert witness is not None, "matches contains only retained block witnesses"
        for run_index, run in enumerate(witness.runs):
            source_end = segment.source_start + segment.end - segment.start
            left = max(segment.source_start, run.source_start)
            right = min(source_end, run.source_end)
            if left >= right:
                continue
            start = segment.start + left - segment.source_start
            end = segment.start + right - segment.source_start
            emitted = text[start:end]
            digest = input_text_sha256(emitted)
            clipped = (left, right) != (run.source_start, run.source_end)
            if clipped:
                if selection is None or index >= len(selection.parts):
                    continue
                live_part = selection.parts[index]
                if live_part.model_copy(update={"text": ""}) != part:
                    continue
                original = live_part.text
                if (
                    len(original) != witness.text_chars
                    or input_text_sha256(original[run.source_start : run.source_end])
                    != run.text_sha256
                    or original[left:right] != emitted
                ):
                    continue
            elif digest != run.text_sha256:
                continue
            rows.append(
                PreparedBlockCopy(
                    start=start,
                    end=end,
                    part_index=index,
                    extraction_id=extraction.result_id,
                    physical_index=summary.physical_index,
                    witness_id=witness.witness_id,
                    run_index=run_index,
                    text_sha256=digest,
                    intersection="clipped-v1" if clipped else None,
                )
            )
    if any(row.intersection is not None for row in rows):
        mixed = _receipt_with_block_rows(receipt, rows, complete_only=True)
        if mixed is not receipt:
            return mixed
        whole = [row for row in rows if row.intersection is None]
        if whole:
            return _receipt_with_block_rows(receipt, whole)
    return _receipt_with_block_rows(receipt, rows)


def _receipt_with_block_rows[
    T: PrimaryInputReceipt
    | MultipartTextInputReceipt
    | SupplementInputReceipt
    | AmendmentInputReceipt
](receipt: T, rows: list[PreparedBlockCopy], *, complete_only: bool = False) -> T:
    if not rows:
        return receipt
    rows_sha256 = _block_rows_sha256(rows)
    lengths = (len(rows),) if complete_only else range(min(32, len(rows)), 0, -1)
    for length in lengths:
        try:
            companion = _block_prefix(rows, length, rows_sha256)
            return cast(
                T,
                type(receipt).model_validate(
                    {**receipt.model_dump(), "block_companion": companion}
                ),
            )
        except ValueError:
            continue
    return receipt

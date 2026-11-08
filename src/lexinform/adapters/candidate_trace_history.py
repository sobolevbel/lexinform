"""Read stored candidate trace identities consistently during lookup, restore and diagnosis."""

import sqlite3
from contextlib import closing
from datetime import UTC
from typing import Literal

from lexinform.adapters.document_source_rows import DocumentSourceRows
from lexinform.adapters.extraction_history import read_extraction_result
from lexinform.adapters.physical_history import PhysicalInventoryReader
from lexinform.document_editions import candidate_edition
from lexinform.json_storage import read_json
from lexinform.models.artifacts import ArtifactMetadata, ArtifactReadReceipt
from lexinform.models.candidate_trace import (
    ArchiveMemberDisposition,
    CandidateDispositionTrace,
    SejmAdoptedText,
    SejmPrintRepresentation,
    archive_conflict_attempt,
    archive_version_main,
    composed_support_sha256,
)
from lexinform.models.document_editions import DocumentEdition
from lexinform.models.documents import DocumentPart
from lexinform.models.extractions import extraction_metadata_sha256
from lexinform.models.physical_inventory import PhysicalRootOrder
from lexinform.models.rcl import rcl_number
from lexinform.models.sejm import TextDocument

CANDIDATE_TRACE_SCHEMA_VERSION = 59


class CandidateTraceRowError(ValueError):
    def __init__(
        self,
        code: Literal[
            "invalid_candidate_trace",
            "candidate_trace_identity_mismatch",
            "candidate_trace_proposal_parent_mismatch",
            "candidate_trace_rcl_proposal_parent_mismatch",
            "candidate_trace_representation_parent_mismatch",
            "candidate_trace_adoption_parent_mismatch",
            "candidate_trace_recommendation_parent_mismatch",
            "candidate_trace_archive_version_parent_mismatch",
            "candidate_trace_archive_conflict_parent_mismatch",
            "candidate_trace_admitted_support_parent_mismatch",
            "candidate_trace_composed_support_parent_mismatch",
            "candidate_trace_duplicate_support_parent_mismatch",
            "candidate_trace_admitted_normative_parent_mismatch",
        ],
    ) -> None:
        self.code = code
        super().__init__("candidate trace: " + code)


def read_candidate_disposition_trace(row: sqlite3.Row) -> CandidateDispositionTrace:
    try:
        trace = CandidateDispositionTrace.model_validate(read_json(row["trace_json"]))
    except ValueError, TypeError, RecursionError:
        raise CandidateTraceRowError("invalid_candidate_trace") from None
    if (trace.trace_id, trace.coverage, trace.candidate_count) != tuple(
        row[key] for key in ("trace_id", "coverage", "candidate_count")
    ):
        raise CandidateTraceRowError("candidate_trace_identity_mismatch")
    return trace


def validate_sejm_proposal_link(
    connection: sqlite3.Connection, trace: CandidateDispositionTrace, observation_id: str
) -> None:
    proposal = trace.sejm_original_proposal
    if proposal is None:
        return
    try:
        reader = DocumentSourceRows(connection)
        source = reader.get_source_observation(observation_id)
        if source is None or (source.term, source.number, source.document, source.selection_id) != (
            proposal.term,
            proposal.parent_print,
            trace.requested_document,
            proposal.selection_id,
        ):
            raise ValueError("proposal source differs from its exact original print")
        record = reader.get_document_selection(proposal.selection_id)
        if record is None:
            raise ValueError("proposal selection is unavailable")
        selection = record.selection
        expected = (
            ("bill", proposal.base_url, proposal.base_sha256),
            ("self_amendment", proposal.proposal_url, proposal.proposal_sha256),
        )
        if (
            selection.state != trace.selection_state
            or selection.version != source.document.version_basis
            or any(part.members for part in selection.parts)
            or tuple((part.role, part.url, part.source_sha256) for part in selection.parts)
            != expected
        ):
            raise ValueError("proposal differs from the original selection's exact parts")
        with closing(connection.cursor()) as cursor:
            cursor.row_factory = sqlite3.Row
            row = cursor.execute(
                "SELECT e.* FROM selection_editions AS link"
                " JOIN document_editions AS e USING (edition_id)"
                " WHERE link.selection_id=? AND e.edition_id=?",
                (proposal.selection_id, proposal.edition_id),
            ).fetchone()
            if row is None:
                raise ValueError("proposal has no exact candidate edition link")
            edition = DocumentEdition.model_validate_json(row["edition_json"])
            if (
                edition.edition_id != row["edition_id"]
                or edition.recorded_at.astimezone(UTC).isoformat() != row["recorded_at"]
                or edition.member_count != 1
                or candidate_edition(record, recorded_at=edition.recorded_at) != edition
            ):
                raise ValueError("proposal edition differs from the original member")
            for index, (_, _, sha) in enumerate(expected):
                row = cursor.execute(
                    "SELECT a.* FROM selection_artifacts AS link"
                    " JOIN artifacts AS a ON a.sha256=link.artifact_sha256"
                    " WHERE link.selection_id=? AND link.part_scope='parts' AND link.part_index=?",
                    (proposal.selection_id, index),
                ).fetchone()
                if row is None or row["sha256"] != sha:
                    raise ValueError("proposal differs from the recorded selected artifact")
                ArtifactMetadata(
                    sha256=row["sha256"],
                    byte_size=row["byte_size"],
                    recorded_at=row["recorded_at"],
                )
    except ValueError, TypeError, RecursionError, IndexError, KeyError, sqlite3.Error:
        raise CandidateTraceRowError("candidate_trace_proposal_parent_mismatch") from None


def validate_rcl_proposal_link(
    connection: sqlite3.Connection, trace: CandidateDispositionTrace, observation_id: str
) -> None:
    proof = trace.rcl_original_proposal
    request = trace.requested_document
    if proof is None:
        return
    code: Literal["candidate_trace_rcl_proposal_parent_mismatch"] = (
        "candidate_trace_rcl_proposal_parent_mismatch"
    )
    if request is None:
        raise CandidateTraceRowError(code)
    candidate = (request, *request.alternatives)[proof.index]
    amendment_url = candidate.amendment_urls[proof.amendment_index]
    reader = DocumentSourceRows(connection)
    try:
        source = reader.get_source_observation(observation_id)
        if source is None or (source.number, source.document) != (
            rcl_number(proof.project_id),
            candidate,
        ):
            raise ValueError("RCL proposal source differs from the selected project")
        record = reader.get_document_selection(source.selection_id)
        if (
            record is None
            or record.selection.state != trace.selection_state
            or record.selection.version != candidate.version_basis
        ):
            raise ValueError("RCL proposal selection differs from the selected candidate")
        edition = candidate_edition(record, recorded_at=source.recorded_at)
        if edition is None or edition.edition_id != proof.edition_id or edition.member_count != 1:
            raise ValueError("RCL proposal edition differs from the selected bill")
        expected = (
            ("bill", candidate.url, proof.base_sha256, proof.base_member_sha256),
            ("self_amendment", amendment_url, proof.proposal_sha256, proof.proposal_member_sha256),
        )
        with closing(connection.cursor()) as cursor:
            cursor.row_factory = sqlite3.Row
            if (
                cursor.execute(
                    "SELECT 1 FROM selection_editions WHERE selection_id=? AND edition_id=?",
                    (record.selection_id, proof.edition_id),
                ).fetchone()
                is None
            ):
                raise ValueError("RCL proposal has no retained edition link")
            for role, url, sha, member_sha in expected:
                positions = [
                    index
                    for index, part in enumerate(record.selection.parts)
                    if part.members
                    and (part.role, part.url, part.source_sha256, part.sha256)
                    == (role, url, sha, member_sha)
                ]
                if (
                    len(positions) != 1
                    or cursor.execute(
                        "SELECT 1 FROM selection_artifacts WHERE selection_id=?"
                        " AND part_scope='parts' AND part_index=? AND artifact_sha256=?",
                        (record.selection_id, positions[0] if positions else -1, sha),
                    ).fetchone()
                    is None
                ):
                    raise ValueError("RCL proposal requires its exact selected archive members")
            row = cursor.execute(
                "SELECT * FROM extraction_results WHERE extraction_id=?",
                (proof.original_extraction_id,),
            ).fetchone()
            if row is None:
                raise ValueError("RCL proposal requires the original Word extraction")
            extraction = read_extraction_result(row)
            if (
                extraction.physical_part_count != 1
                or extraction.rejected_count
                or len(extraction.selected_parts) != 1
                or extraction.omitted_selected_parts
            ):
                raise ValueError("RCL proposal original must be a complete standalone extraction")
            (part,) = extraction.selected_parts
            if (
                part.format != "word"
                or part.coordinates != "word-blocks-v1"
                or part.physical_index != 0
                or part.member_path_sha256 != extraction_metadata_sha256(())
                or part.sha256 != extraction.artifact_sha256
                or part.error_count
                or part.loss_count
                or part.text_sha256 != extraction.output_text_sha256
                or part.text_chars != extraction.output_chars
                or cursor.execute(
                    "SELECT 1 FROM artifact_reads WHERE requested_url=? AND artifact_sha256=?",
                    (proof.original_url, extraction.artifact_sha256),
                ).fetchone()
                is None
            ):
                raise ValueError("RCL proposal requires the exact original Word body read")
    except ValueError, TypeError, RecursionError, IndexError, KeyError, sqlite3.Error:
        raise CandidateTraceRowError(code) from None


def validate_committee_recommendation_link(
    connection: sqlite3.Connection, trace: CandidateDispositionTrace, observation_id: str
) -> None:
    proof = trace.sejm_committee_recommendation
    if proof is None:
        return
    try:
        reader = DocumentSourceRows(connection)
        report = reader.get_source_observation(observation_id)
        resolution = reader.get_source_observation(proof.resolution_observation_id)
        if (
            report is None
            or resolution is None
            or report.document != trace.requested_document
            or resolution.document.kind != "senate_amendments"
            or not resolution.document.url.startswith(proof.resolution_url_prefix)
            or resolution.document.senate is None
            or resolution.document.senate.scan
            or (resolution.document.senate.sha256, resolution.document.senate.adopted_on)
            != (proof.resolution_sha256, proof.resolution_date)
        ):
            raise ValueError("Recommendation requires its two exact source observations")
        with closing(connection.cursor()) as cursor:
            cursor.row_factory = sqlite3.Row
            for source, sha, read_id in (
                (report, proof.report_sha256, proof.report_read_id),
                (resolution, proof.resolution_sha256, proof.resolution_read_id),
            ):
                record = reader.get_document_selection(source.selection_id)
                if (
                    (source.term, source.number, source.purpose)
                    != (proof.term, proof.number, "analysis")
                    or record is None
                    or (source is report and record.selection.state != trace.selection_state)
                    or len(record.selection.parts) != 1
                    or (part := record.selection.parts[0]).members
                    or (part.url, part.source_sha256) != (source.document.url, sha)
                ):
                    raise ValueError("Recommendation source differs from its singleton selection")
                row = cursor.execute(
                    "SELECT r.*, a.byte_size AS artifact_size FROM artifact_reads AS r"
                    " JOIN artifacts AS a ON a.sha256=r.artifact_sha256"
                    " JOIN selection_artifacts AS link ON link.artifact_sha256=a.sha256"
                    " WHERE r.read_id=? AND link.selection_id=?"
                    " AND link.part_scope='parts' AND link.part_index=0",
                    (read_id, source.selection_id),
                ).fetchone()
                if row is None or (row["requested_url"], row["artifact_sha256"]) != (
                    source.document.url,
                    sha,
                ):
                    raise ValueError("Recommendation requires an exact artifact read")
                receipt = ArtifactReadReceipt(
                    requested_url=source.document.url,
                    sha256=sha,
                    byte_size=row["byte_size"],
                    recorded_at=row["recorded_at"],
                )
                if receipt.read_id != read_id or receipt.byte_size != row["artifact_size"]:
                    raise ValueError("Recommendation read differs from its artifact")
    except ValueError, TypeError, RecursionError, IndexError, KeyError, sqlite3.Error:
        raise CandidateTraceRowError("candidate_trace_recommendation_parent_mismatch") from None


def _validate_superseded_draft(
    reader: DocumentSourceRows, cursor: sqlite3.Cursor, proof: SejmAdoptedText
) -> None:
    prior = proof.supersedes
    if prior is None:
        return
    url = f"https://api.sejm.gov.pl/sejm/term{proof.term}/prints/{prior.report}/{prior.report}.pdf"
    observation = reader.get_source_observation(prior.observation_id)
    if (
        observation is None
        or (observation.term, observation.number, observation.purpose)
        != (proof.term, proof.number, "analysis")
        or (observation.document.kind, observation.document.url) != ("committee_report", url)
    ):
        raise ValueError("Supersession requires the same owner's exact report observation")
    record = reader.get_document_selection(observation.selection_id)
    if (
        record is None
        or record.selection.question != "sejm_committee_draft"
        or record.selection.policy_version != prior.profile
        or len(record.selection.parts) != 1
        or (part := record.selection.parts[0]).members
        or (
            part.role,
            part.url,
            part.sha256,
            part.text_sha256,
            part.page_start,
            part.page_end,
            part.pages,
        )
        != (
            "bill",
            url,
            prior.pdf_sha256,
            prior.text_sha256,
            prior.first_page,
            prior.last_page,
            prior.last_page,
        )
    ):
        raise ValueError("Supersession requires the proved range inside the report")
    row = cursor.execute(
        "SELECT e.*, r.read_id, r.byte_size, r.recorded_at AS read_at, a.byte_size AS artifact_size"
        " FROM selection_editions AS link JOIN document_editions AS e USING (edition_id)"
        " JOIN selection_artifacts AS part USING (selection_id)"
        " JOIN artifacts AS a ON a.sha256=part.artifact_sha256"
        " JOIN artifact_reads AS r ON r.artifact_sha256=a.sha256"
        " WHERE link.selection_id=? AND e.edition_id=?"
        " AND part.part_scope='parts' AND part.part_index=0"
        " AND r.requested_url=? AND a.sha256=?",
        (record.selection_id, prior.edition_id, url, prior.pdf_sha256),
    ).fetchone()
    if row is None:
        raise ValueError("Supersession requires an exact prior edition and report body read")
    edition = DocumentEdition.model_validate_json(row["edition_json"])
    receipt = ArtifactReadReceipt(
        requested_url=url,
        sha256=prior.pdf_sha256,
        byte_size=row["byte_size"],
        recorded_at=row["read_at"],
    )
    if (
        edition.edition_id != prior.edition_id
        or edition.recorded_at.astimezone(UTC).isoformat() != row["recorded_at"]
        or candidate_edition(record, recorded_at=edition.recorded_at) != edition
        or receipt.read_id != row["read_id"]
        or receipt.byte_size != row["artifact_size"]
    ):
        raise ValueError("Supersession parents differ from the recorded report edition")


def _archive_member_in_execution(
    reader: PhysicalInventoryReader, order: PhysicalRootOrder, slot: int
) -> tuple[tuple[int, ...], str, str]:
    if slot >= len(order.physical):
        raise ValueError("archive comparison slot is unavailable")
    owner, text_sha256, _ = order.physical[slot]
    if owner >= len(order.contexts):
        raise ValueError("archive comparison slot has no retained occurrence")
    _, _, physical_slot, unit_id = order.contexts[owner]
    unit = reader.typed(unit_id, "unit")
    path: list[int] = []
    while owner:
        parent, ordinal, _, _ = order.contexts[owner]
        if parent is None or ordinal is None or len(path) == 4:
            raise ValueError("archive comparison occurrence has no bounded root path")
        path.append(ordinal)
        owner = parent
    if physical_slot != slot or not path or unit.body_sha256 is None:
        raise ValueError("archive comparison occurrence differs from its physical slot")
    return tuple(reversed(path)), unit.body_sha256, text_sha256


def _archive_peer_in_execution(
    reader: PhysicalInventoryReader, order: PhysicalRootOrder, row: ArchiveMemberDisposition
) -> None:
    slot = row.physical_part_index
    if slot is None or _archive_member_in_execution(reader, order, slot) != (
        row.ordinals,
        row.leaf_sha256,
        row.text_sha256,
    ):
        raise ValueError("archive comparison differs from its physical member")


def _validate_archive_version_link(
    connection: sqlite3.Connection, trace: CandidateDispositionTrace, observation_id: str
) -> None:
    evidence = trace.archive_version_decision
    if evidence is None:
        return
    try:
        main = archive_version_main(trace, evidence)
        requested = trace.requested_document
        if requested is None:
            raise ValueError("archive comparison requires its requested source")
        candidate = (requested, *requested.alternatives)[main.index]
        source_reader = DocumentSourceRows(connection)
        source = source_reader.get_source_observation(observation_id)
        if source is None or source.document.url != candidate.url:
            raise ValueError("archive comparison source differs from its selected root")
        selection = source_reader.get_document_selection(source.selection_id)
        if selection is None or selection.selection.state != trace.selection_state:
            raise ValueError("archive comparison selection is unavailable")
        selected = [
            part
            for part in selection.selection.parts
            if part.members
            and (part.source_sha256, part.sha256, part.text_sha256)
            == (main.root_sha256, main.leaf_sha256, main.text_sha256)
        ]
        if len(selected) != 1:
            raise ValueError("archive comparison main differs from its selected member")

        old_factory = connection.row_factory
        connection.row_factory = sqlite3.Row
        try:
            saved = connection.execute(
                "SELECT * FROM physical_captures WHERE capture_id=?", (evidence.capture_id,)
            ).fetchone()
            if saved is None:
                raise ValueError("archive comparison physical capture is unavailable")
            reader = PhysicalInventoryReader(connection)
            capture = reader.capture(saved)
            if capture.artifact_sha256 != main.root_sha256 or capture.extraction_id is None:
                raise ValueError("archive comparison capture differs from its extraction root")
            linked = connection.execute(
                "SELECT 1 FROM selection_extractions WHERE selection_id=? AND extraction_id=?",
                (source.selection_id, capture.extraction_id),
            ).fetchone()
            read = connection.execute(
                "SELECT 1 FROM artifact_reads WHERE requested_url=? AND artifact_sha256=?",
                (candidate.url, capture.artifact_sha256),
            ).fetchone()
            if linked is None or read is None:
                raise ValueError("archive comparison is missing the source extraction or body read")
            execution = reader.typed(capture.execution_id, "execution")
            order = execution.metadata
            if execution.coverage != "complete" or not isinstance(order, PhysicalRootOrder):
                raise ValueError("archive comparison requires retained physical execution")
            _archive_peer_in_execution(reader, order, main)
            _archive_peer_in_execution(reader, order, evidence.peer)
        finally:
            connection.row_factory = old_factory
    except ValueError, TypeError, RecursionError, IndexError, KeyError, sqlite3.Error:
        raise CandidateTraceRowError("candidate_trace_archive_version_parent_mismatch") from None


def _validate_archive_conflict_link(
    connection: sqlite3.Connection, trace: CandidateDispositionTrace, observation_id: str
) -> None:
    evidence = trace.archive_version_conflict
    if evidence is None:
        return
    try:
        parent = archive_conflict_attempt(trace, evidence)
        requested = trace.requested_document
        if requested is None:
            raise ValueError("archive conflict requires its requested source")
        candidates = (requested, *requested.alternatives)
        candidate = candidates[evidence.index]
        selected = next((row for row in trace.dispositions if row.outcome == "selected"), None)
        source_reader = DocumentSourceRows(connection)
        source = source_reader.get_source_observation(observation_id)
        expected = candidates[selected.index] if selected is not None else requested
        if source is None or source.document != expected:
            raise ValueError("archive conflict differs from its exact source observation")
        selection = source_reader.get_document_selection(source.selection_id)
        if (
            selection is None
            or selection.selection.state != trace.selection_state
            or any(
                part.source_sha256 == parent.observed_sha256 or part.url == candidate.url
                for part in selection.selection.parts
            )
        ):
            raise ValueError("archive conflict cannot certify a selected member of its root")

        old_factory = connection.row_factory
        connection.row_factory = sqlite3.Row
        try:
            saved = connection.execute(
                "SELECT * FROM physical_captures WHERE capture_id=?", (evidence.capture_id,)
            ).fetchone()
            if saved is None:
                raise ValueError("archive conflict physical capture is unavailable")
            reader = PhysicalInventoryReader(connection)
            capture = reader.capture(saved)
            if capture.artifact_sha256 != parent.observed_sha256 or capture.extraction_id is None:
                raise ValueError("archive conflict capture differs from its extraction root")
            extraction_row = connection.execute(
                "SELECT * FROM extraction_results WHERE extraction_id=?", (capture.extraction_id,)
            ).fetchone()
            read = connection.execute(
                "SELECT 1 FROM artifact_reads WHERE requested_url=? AND artifact_sha256=?",
                (candidate.url, capture.artifact_sha256),
            ).fetchone()
            if extraction_row is None or read is None:
                raise ValueError("archive conflict requires its exact extraction and body read")
            extraction = read_extraction_result(extraction_row)
            selected_parts = {row.physical_index: row for row in extraction.selected_parts}
            if (
                extraction.artifact_sha256 != capture.artifact_sha256
                or extraction.output_chars != 0
                or extraction.selected_part_count != 2
                or extraction.omitted_selected_parts
                or set(evidence.compared_slots) != set(selected_parts)
            ):
                raise ValueError(
                    "archive conflict differs from its exact compared extraction parts"
                )
            execution = reader.typed(capture.execution_id, "execution")
            order = execution.metadata
            if execution.coverage != "complete" or not isinstance(order, PhysicalRootOrder):
                raise ValueError("archive conflict requires retained physical execution")
            for slot in evidence.compared_slots:
                part = selected_parts[slot]
                _, leaf_sha, text_sha = _archive_member_in_execution(reader, order, slot)
                if (leaf_sha, text_sha) != (part.sha256, part.text_sha256):
                    raise ValueError("archive conflict extraction differs from its physical member")
            members = trace.archive_members
            assert members is not None, "archive_conflict_attempt requires an archive profile"
            for row in members.rows:
                if (row.phase, row.index, row.stage) == (
                    "version_comparison",
                    evidence.index,
                    "policy",
                ) and row.physical_part_index in evidence.compared_slots:
                    _archive_peer_in_execution(reader, order, row)
        finally:
            connection.row_factory = old_factory
    except ValueError, TypeError, RecursionError, IndexError, KeyError, sqlite3.Error:
        raise CandidateTraceRowError("candidate_trace_archive_conflict_parent_mismatch") from None


def _admitted_selection(
    connection: sqlite3.Connection,
    trace: CandidateDispositionTrace,
    observation_id: str,
    index: int,
    code: Literal[
        "candidate_trace_admitted_support_parent_mismatch",
        "candidate_trace_composed_support_parent_mismatch",
        "candidate_trace_duplicate_support_parent_mismatch",
        "candidate_trace_admitted_normative_parent_mismatch",
    ],
) -> tuple[TextDocument, tuple[DocumentPart, ...]]:
    requested = trace.requested_document
    if requested is None:
        raise CandidateTraceRowError(code)
    candidate = (requested, *requested.alternatives)[index]
    reader = DocumentSourceRows(connection)
    source = reader.get_source_observation(observation_id)
    if source is None or source.document != candidate:
        raise CandidateTraceRowError(code)
    record = reader.get_document_selection(source.selection_id)
    if record is None or record.selection.state != trace.selection_state:
        raise CandidateTraceRowError(code)
    return candidate, record.selection.parts


def _validate_admitted_support_link(
    connection: sqlite3.Connection, trace: CandidateDispositionTrace, observation_id: str
) -> None:
    evidence = trace.admitted_support
    if evidence is None:
        return
    code: Literal["candidate_trace_admitted_support_parent_mismatch"] = (
        "candidate_trace_admitted_support_parent_mismatch"
    )
    candidate, selected_parts = _admitted_selection(
        connection, trace, observation_id, evidence.index, code
    )
    for row in evidence.rows:
        url = candidate.extra_urls[row.extra_index]
        parts = [part for part in selected_parts if part.url == url]
        if (
            len(parts) != 1
            or parts[0].members
            or (parts[0].role, parts[0].sha256, parts[0].text_sha256)
            != (row.role, row.file_sha256, row.text_sha256)
        ):
            raise CandidateTraceRowError(code)


def _validate_duplicate_support_link(
    connection: sqlite3.Connection, trace: CandidateDispositionTrace, observation_id: str
) -> None:
    exclusions = trace.supporting_exclusions
    if exclusions is None:
        return
    duplicates = [row for row in exclusions.rows if row.reason == "duplicate_standalone"]
    if not duplicates:
        return
    code: Literal["candidate_trace_duplicate_support_parent_mismatch"] = (
        "candidate_trace_duplicate_support_parent_mismatch"
    )
    candidate, parts = _admitted_selection(
        connection, trace, observation_id, duplicates[0].index, code
    )
    for row in duplicates:
        position = row.duplicate_of_part
        if position is None or position >= len(parts):
            raise CandidateTraceRowError(code)
        part = parts[position]
        earlier_urls = {
            candidate.url,
            *candidate.required_urls,
            *candidate.normative_urls,
            *candidate.amendment_urls,
            *candidate.extra_urls[: row.extra_index],
        }
        if (
            part.members
            or (part.sha256, part.text_sha256) != (row.file_sha256, row.duplicate_text_sha256)
            or part.url not in earlier_urls
            or any(item.url == candidate.extra_urls[row.extra_index] for item in parts)
        ):
            raise CandidateTraceRowError(code)


def _validate_composed_support_link(
    connection: sqlite3.Connection, trace: CandidateDispositionTrace, observation_id: str
) -> None:
    evidence = trace.composed_support
    if evidence is None:
        return
    code: Literal["candidate_trace_composed_support_parent_mismatch"] = (
        "candidate_trace_composed_support_parent_mismatch"
    )
    candidate, selected_parts = _admitted_selection(
        connection, trace, observation_id, evidence.index, code
    )
    for row in evidence.rows:
        url = candidate.extra_urls[row.extra_index]
        indices = [index for index, part in enumerate(selected_parts) if part.url == url]
        end = row.first_part + row.part_count
        if (
            end > len(selected_parts)
            or indices != list(range(row.first_part, end))
            or composed_support_sha256(selected_parts[row.first_part : end]) != row.parts_sha256
        ):
            raise CandidateTraceRowError(code)


def _validate_admitted_normative_link(
    connection: sqlite3.Connection, trace: CandidateDispositionTrace, observation_id: str
) -> None:
    evidence = trace.admitted_normative_annexes
    if evidence is None:
        return
    code: Literal["candidate_trace_admitted_normative_parent_mismatch"] = (
        "candidate_trace_admitted_normative_parent_mismatch"
    )
    candidate, selected_parts = _admitted_selection(
        connection, trace, observation_id, evidence.index, code
    )
    for row in evidence.rows:
        url = candidate.normative_urls[row.normative_index]
        parts = [part for part in selected_parts if part.url == url]
        if len(parts) != 1:
            raise CandidateTraceRowError(code)
        (part,) = parts
        if (
            part.members
            or part.losses
            or part.text_start != 0
            or part.text_end is None
            or part.text_end <= 0
            or (part.role, part.sha256, part.text_sha256)
            != ("normative_annex", row.file_sha256, row.text_sha256)
        ):
            raise CandidateTraceRowError(code)


def validate_candidate_trace_link(
    connection: sqlite3.Connection, trace: CandidateDispositionTrace, observation_id: str
) -> None:
    _validate_admitted_support_link(connection, trace, observation_id)
    _validate_composed_support_link(connection, trace, observation_id)
    _validate_duplicate_support_link(connection, trace, observation_id)
    _validate_admitted_normative_link(connection, trace, observation_id)
    _validate_archive_version_link(connection, trace, observation_id)
    _validate_archive_conflict_link(connection, trace, observation_id)
    validate_sejm_proposal_link(connection, trace, observation_id)
    validate_rcl_proposal_link(connection, trace, observation_id)
    validate_committee_recommendation_link(connection, trace, observation_id)
    proof = trace.sejm_print_representation or trace.sejm_adopted_text
    if proof is None:
        return
    try:
        reader = DocumentSourceRows(connection)
        source = reader.get_source_observation(observation_id)
        if source is None or (source.term, source.number, source.document, source.selection_id) != (
            proof.term,
            proof.number,
            trace.requested_document,
            proof.selection_id,
        ):
            raise ValueError("representation source differs from its original print")
        record = reader.get_document_selection(proof.selection_id)
        if record is None:
            raise ValueError("representation selection is unavailable")
        selection = record.selection
        if (
            selection.state != trace.selection_state
            or selection.version != source.document.version_basis
            or len(selection.parts) != 1
            or (part := selection.parts[0]).members
            or (part.role, part.url, part.source_sha256, part.text_sha256)
            != ("bill", proof.pdf_url, proof.pdf_sha256, proof.pdf_text_sha256)
        ):
            raise ValueError("representation differs from its selected PDF")
        with closing(connection.cursor()) as cursor:
            cursor.row_factory = sqlite3.Row
            parent = cursor.execute(
                "SELECT e.*, a.artifact_sha256 FROM selection_editions AS link"
                " JOIN document_editions AS e USING (edition_id)"
                " JOIN selection_artifacts AS a USING (selection_id)"
                " WHERE link.selection_id=? AND e.edition_id=?"
                " AND a.part_scope='parts' AND a.part_index=0",
                (proof.selection_id, proof.edition_id),
            ).fetchone()
            if parent is None or parent["artifact_sha256"] != proof.pdf_sha256:
                raise ValueError("representation requires an exact edition and PDF artifact link")
            edition = DocumentEdition.model_validate_json(parent["edition_json"])
            if (
                edition.edition_id != proof.edition_id
                or edition.recorded_at.astimezone(UTC).isoformat() != parent["recorded_at"]
                or candidate_edition(record, recorded_at=edition.recorded_at) != edition
            ):
                raise ValueError("representation edition differs from its recorded selection")
            reads = [(proof.pdf_url, proof.pdf_sha256)]
            if isinstance(proof, SejmPrintRepresentation):
                reads.append((proof.docx_url, proof.docx_sha256))
            for url, sha in reads:
                row = cursor.execute(
                    "SELECT r.*, a.byte_size AS artifact_size FROM artifact_reads AS r"
                    " JOIN artifacts AS a ON a.sha256=r.artifact_sha256"
                    " WHERE r.requested_url=? AND r.artifact_sha256=?",
                    (url, sha),
                ).fetchone()
                if row is None:
                    raise ValueError("representation requires both exact body reads")
                receipt = ArtifactReadReceipt(
                    requested_url=url,
                    sha256=sha,
                    byte_size=row["byte_size"],
                    recorded_at=row["recorded_at"],
                )
                if receipt.read_id != row["read_id"] or receipt.byte_size != row["artifact_size"]:
                    raise ValueError("representation body read differs from its artifact")
            if isinstance(proof, SejmAdoptedText):
                _validate_superseded_draft(reader, cursor, proof)
                return
            row = cursor.execute(
                "SELECT * FROM extraction_results WHERE extraction_id=?",
                (proof.docx_extraction_id,),
            ).fetchone()
            if row is None:
                raise ValueError("representation requires its DOCX extraction")
            extraction = read_extraction_result(row)
            if (extraction.artifact_sha256, extraction.output_text_sha256) != (
                proof.docx_sha256,
                proof.docx_text_sha256,
            ) or proof.core_chars > extraction.output_chars:
                raise ValueError("representation DOCX extraction differs from the proof")
    except ValueError, TypeError, RecursionError, IndexError, KeyError, sqlite3.Error:
        raise CandidateTraceRowError(
            "candidate_trace_representation_parent_mismatch"
            if isinstance(proof, SejmPrintRepresentation)
            else "candidate_trace_adoption_parent_mismatch"
        ) from None


def validate_candidate_trace_links(
    connection: sqlite3.Connection, trace: CandidateDispositionTrace
) -> None:
    code: Literal[
        "candidate_trace_adoption_parent_mismatch",
        "candidate_trace_recommendation_parent_mismatch",
        "candidate_trace_representation_parent_mismatch",
        "candidate_trace_proposal_parent_mismatch",
        "candidate_trace_rcl_proposal_parent_mismatch",
        "candidate_trace_archive_version_parent_mismatch",
        "candidate_trace_archive_conflict_parent_mismatch",
        "candidate_trace_admitted_support_parent_mismatch",
        "candidate_trace_composed_support_parent_mismatch",
        "candidate_trace_duplicate_support_parent_mismatch",
        "candidate_trace_admitted_normative_parent_mismatch",
    ]
    if trace.admitted_normative_annexes is not None:
        code = "candidate_trace_admitted_normative_parent_mismatch"
    elif trace.supporting_exclusions is not None and any(
        row.reason == "duplicate_standalone" for row in trace.supporting_exclusions.rows
    ):
        code = "candidate_trace_duplicate_support_parent_mismatch"
    elif trace.composed_support is not None:
        code = "candidate_trace_composed_support_parent_mismatch"
    elif trace.admitted_support is not None:
        code = "candidate_trace_admitted_support_parent_mismatch"
    elif trace.archive_version_conflict is not None:
        code = "candidate_trace_archive_conflict_parent_mismatch"
    elif trace.archive_version_decision is not None:
        code = "candidate_trace_archive_version_parent_mismatch"
    elif trace.sejm_committee_recommendation is not None:
        code = "candidate_trace_recommendation_parent_mismatch"
    elif trace.sejm_adopted_text is not None:
        code = "candidate_trace_adoption_parent_mismatch"
    elif trace.sejm_print_representation is not None:
        code = "candidate_trace_representation_parent_mismatch"
    elif trace.sejm_original_proposal is not None:
        code = "candidate_trace_proposal_parent_mismatch"
    elif trace.rcl_original_proposal is not None:
        code = "candidate_trace_rcl_proposal_parent_mismatch"
    else:
        return
    try:
        with closing(connection.cursor()) as cursor:
            cursor.row_factory = sqlite3.Row
            links = cursor.execute(
                "SELECT observation_id FROM source_candidate_traces WHERE trace_id=?",
                (trace.trace_id,),
            )
            found = False
            for row in links:
                found = True
                validate_candidate_trace_link(connection, trace, row["observation_id"])
            if not found:
                raise CandidateTraceRowError(code)
    except sqlite3.Error:
        raise CandidateTraceRowError(code) from None


def validate_candidate_disposition_traces(connection: sqlite3.Connection) -> None:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        for row in cursor.execute("SELECT * FROM candidate_disposition_traces"):
            trace = read_candidate_disposition_trace(row)
            validate_candidate_trace_links(connection, trace)
    finally:
        cursor.close()
    if (
        connection.execute("PRAGMA foreign_key_check(source_candidate_traces)").fetchone()
        is not None
    ):
        raise ValueError("candidate trace link has a broken reference")

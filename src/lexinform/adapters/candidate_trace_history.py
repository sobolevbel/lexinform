"""Read stored candidate trace identities consistently during lookup, restore and diagnosis."""

import sqlite3
from contextlib import closing
from datetime import UTC
from typing import Literal

from lexinform.adapters.content_source_refs import ContentSourceRows
from lexinform.adapters.extraction_history import read_extraction_result
from lexinform.document_editions import candidate_edition
from lexinform.json_storage import read_json
from lexinform.models.artifacts import ArtifactMetadata, ArtifactReadReceipt
from lexinform.models.candidate_trace import CandidateDispositionTrace
from lexinform.models.document_editions import DocumentEdition

CANDIDATE_TRACE_SCHEMA_VERSION = 59


class CandidateTraceRowError(ValueError):
    def __init__(
        self,
        code: Literal[
            "invalid_candidate_trace",
            "candidate_trace_identity_mismatch",
            "candidate_trace_proposal_parent_mismatch",
            "candidate_trace_representation_parent_mismatch",
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
        reader = ContentSourceRows(connection)
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


def validate_candidate_trace_link(
    connection: sqlite3.Connection, trace: CandidateDispositionTrace, observation_id: str
) -> None:
    validate_sejm_proposal_link(connection, trace, observation_id)
    proof = trace.sejm_print_representation
    if proof is None:
        return
    try:
        reader = ContentSourceRows(connection)
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
            for url, sha in (
                (proof.pdf_url, proof.pdf_sha256),
                (proof.docx_url, proof.docx_sha256),
            ):
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
        raise CandidateTraceRowError("candidate_trace_representation_parent_mismatch") from None


def validate_candidate_trace_links(
    connection: sqlite3.Connection, trace: CandidateDispositionTrace
) -> None:
    if trace.sejm_original_proposal is None and trace.sejm_print_representation is None:
        return
    code: Literal[
        "candidate_trace_proposal_parent_mismatch", "candidate_trace_representation_parent_mismatch"
    ] = (
        "candidate_trace_representation_parent_mismatch"
        if trace.sejm_print_representation is not None
        else "candidate_trace_proposal_parent_mismatch"
    )
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

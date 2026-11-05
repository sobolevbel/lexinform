"""Read stored candidate trace identities consistently during lookup, restore and diagnosis."""

import sqlite3
from contextlib import closing
from datetime import UTC
from typing import Literal

from lexinform.adapters.content_source_refs import ContentSourceRows
from lexinform.document_editions import candidate_edition
from lexinform.json_storage import read_json
from lexinform.models.artifacts import ArtifactMetadata
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


def validate_sejm_proposal_links(
    connection: sqlite3.Connection, trace: CandidateDispositionTrace
) -> None:
    if trace.sejm_original_proposal is None:
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
                validate_sejm_proposal_link(connection, trace, row["observation_id"])
            if not found:
                raise CandidateTraceRowError("candidate_trace_proposal_parent_mismatch")
    except sqlite3.Error:
        raise CandidateTraceRowError("candidate_trace_proposal_parent_mismatch") from None


def validate_candidate_disposition_traces(connection: sqlite3.Connection) -> None:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        for row in cursor.execute("SELECT * FROM candidate_disposition_traces"):
            trace = read_candidate_disposition_trace(row)
            validate_sejm_proposal_links(connection, trace)
    finally:
        cursor.close()
    if (
        connection.execute("PRAGMA foreign_key_check(source_candidate_traces)").fetchone()
        is not None
    ):
        raise ValueError("candidate trace link has a broken reference")

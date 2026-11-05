"""Read stored candidate trace identities consistently during lookup, restore and diagnosis."""

import sqlite3
from typing import Literal

from lexinform.json_storage import read_json
from lexinform.models.candidate_trace import CandidateDispositionTrace

CANDIDATE_TRACE_SCHEMA_VERSION = 59


class CandidateTraceRowError(ValueError):
    def __init__(
        self, code: Literal["invalid_candidate_trace", "candidate_trace_identity_mismatch"]
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


def validate_candidate_disposition_traces(connection: sqlite3.Connection) -> None:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        for row in cursor.execute("SELECT * FROM candidate_disposition_traces"):
            read_candidate_disposition_trace(row)
    finally:
        cursor.close()
    if (
        connection.execute("PRAGMA foreign_key_check(source_candidate_traces)").fetchone()
        is not None
    ):
        raise ValueError("candidate trace link has a broken reference")

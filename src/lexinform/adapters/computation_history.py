"""Decode immutable computation JSON with its indexed SQL identity."""

import sqlite3

from lexinform.json_storage import read_json
from lexinform.models.computations import ComputationResult


class ComputationRowError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def read_computation_result(row: sqlite3.Row) -> ComputationResult:
    try:
        result = ComputationResult.model_validate(read_json(row["result_json"]))
    except ValueError, TypeError, RecursionError:
        raise ComputationRowError("invalid_computation_result") from None
    if (
        result.result_id,
        result.provider,
        result.batch_id,
        result.custom_id,
        result.term,
        result.number,
    ) != tuple(
        row[key] for key in ("result_id", "provider", "batch_id", "custom_id", "term", "number")
    ):
        raise ComputationRowError("computation_columns_mismatch")
    return result

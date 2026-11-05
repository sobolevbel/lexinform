"""Validate body-free memo pointers without inventing unavailable caller contexts."""

import sqlite3
from collections.abc import Iterator

from lexinform.adapters.computation_history import read_computation_result
from lexinform.review_memos import ReviewMemoError, read_review_memo, review_memo_parent


def validate_review_memo_row(
    connection: sqlite3.Connection, raw: object, term: object, number: object
) -> None:
    reference = read_review_memo(raw)
    if reference is None:
        return
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        row = cursor.execute(
            "SELECT * FROM computation_results WHERE result_id=?", (reference.modern_review_ref,)
        ).fetchone()
        result = read_computation_result(row) if row is not None else None
        review_memo_parent(reference, result)
        if (
            type(term) is not int
            or not isinstance(number, str)
            or result is None
            or result.number != number
        ):
            raise ReviewMemoError()
    except ValueError, TypeError, IndexError, KeyError, RecursionError, sqlite3.Error:
        raise ReviewMemoError() from None
    finally:
        cursor.close()


def review_memo_issues(connection: sqlite3.Connection) -> Iterator[tuple[str, str]]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        for row in cursor.execute("SELECT key, record_json, term, number FROM analysis_memo"):
            try:
                validate_review_memo_row(connection, row["record_json"], row["term"], row["number"])
            except ReviewMemoError:
                yield "invalid_review_memo", "analysis_memo/" + str(row["key"])
    finally:
        cursor.close()


def validate_review_memos(connection: sqlite3.Connection) -> None:
    if next(review_memo_issues(connection), None) is not None:
        raise ReviewMemoError()

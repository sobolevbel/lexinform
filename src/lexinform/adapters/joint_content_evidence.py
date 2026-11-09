"""Bind a frozen joint reply to its exact retained comparison application."""

import sqlite3
from contextlib import closing
from datetime import datetime

from lexinform.adapters.computation_history import read_result_application
from lexinform.adapters.content_source_refs import ContentSourceRows
from lexinform.models import Bill, JointRecord, ResultApplicationTarget

_JOINT_TARGET = ResultApplicationTarget(kind="bill", ref="joint")
_USAGE_FIELDS = frozenset(
    ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
)


def joint_comparison_application_id(
    db: sqlite3.Connection, bill: Bill, *, recorded_at: datetime
) -> int | None:
    frozen = bill.joint
    if frozen is None or frozen.computation_id is None or frozen.request_sha256 is None:
        return None
    result = ContentSourceRows(db).get_computation_result(frozen.computation_id)
    if (
        result is None
        or result.validation != "validated"
        or result.purpose != "joint"
        or result.generation is not None
        or result.input_binding is None
        or (result.input_binding.term, result.input_binding.number) != (bill.term, bill.number)
        or (result.term, result.number, result.request_sha256)
        != (bill.term, bill.number, frozen.request_sha256)
        or not isinstance(result.record, JointRecord)
        or result.collected_at.utcoffset() is None
    ):
        return None
    paid = result.record
    if frozen != paid and frozen != paid.model_copy(update=dict.fromkeys(_USAGE_FIELDS, 0)):
        return None
    with closing(db.cursor()) as cursor:
        cursor.row_factory = sqlite3.Row
        rows = cursor.execute(
            "SELECT * FROM result_applications WHERE result_id=? AND term=? AND number=?"
            " AND generation IS NULL AND phase='projection'"
            " AND json_extract(application_json,'$.target.kind')=?"
            " AND json_extract(application_json,'$.target.ref')=? LIMIT 2",
            (result.result_id, bill.term, bill.number, _JOINT_TARGET.kind, _JOINT_TARGET.ref),
        ).fetchall()
    if len(rows) != 1:
        return None
    application = read_result_application(rows[0])
    if (
        application.target != _JOINT_TARGET
        or application.outcome != "applied"
        or application.reason != "retained_result"
        or application.created_at.utcoffset() is None
        or not result.collected_at <= application.created_at <= recorded_at
    ):
        return None
    return int(rows[0]["id"])

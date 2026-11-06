"""Decode the durable status-change row shared by repository and editorial validation."""

import json
import sqlite3
from datetime import datetime

from lexinform.models import AmendmentsRecord, Stage, StatusChange, SupplementRecord


def read_status_change(row: sqlite3.Row) -> StatusChange:
    supplements = [
        SupplementRecord.model_validate(value)
        for value in json.loads(row["supplements_json"] or "[]")
    ]
    return StatusChange(
        id=int(row["id"]),
        term=int(row["term"]),
        consultation_opened=bool(row["consultation_opened"]),
        number=row["number"],
        old_fingerprint=row["old_fingerprint"],
        new_fingerprint=row["new_fingerprint"],
        new_stages=[Stage.model_validate(s) for s in json.loads(row["new_stages_json"])],
        closure_detected=bool(row["closure_detected"]),
        decision_changed=bool(row["decision_changed"]),
        passed=None if row["passed"] is None else bool(row["passed"]),
        content_changed=bool(row["content_changed"]),
        withdrawn=bool(row["withdrawn"]),
        discontinued=bool(row["discontinued"]),
        amendments=(
            AmendmentsRecord.model_validate_json(row["amendments_json"])
            if row["amendments_json"]
            else None
        ),
        supplements=supplements,
        detected_at=datetime.fromisoformat(row["detected_at"]),
    )

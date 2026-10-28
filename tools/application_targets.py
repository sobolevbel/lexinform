"""Observe recorded target linkage without executing or revalidating historical applications."""

import sqlite3
from typing import Any

from lexinform.models import ResultApplication

_PHYSICAL = {
    "status_change": ("status_changes", "id"),
    "publication": ("publications", "id"),
    "command": ("commands", "update_id"),
}


def application_target(
    connection: sqlite3.Connection, application: ResultApplication, tables: set[str]
) -> dict[str, Any] | None:
    target = application.target
    if target is None:
        return None
    observation: dict[str, Any] = {"kind": target.kind, "status": "opaque"}
    if target.kind in _PHYSICAL:
        try:
            identity = int(target.ref)
        except ValueError:
            return observation
        if str(identity) != target.ref or not -(2**63) <= identity < 2**63:
            return observation
        table, key = _PHYSICAL[target.kind]
        observation["entity_id"] = target.ref
        if table not in tables:
            return {**observation, "status": "table_unavailable"}
        exists = (
            connection.execute(f"SELECT 1 FROM {table} WHERE {key}=?", (identity,)).fetchone()
            is not None
        )
        return {**observation, "status": "present" if exists else "row_unavailable"}
    purpose, _, source_key = target.ref.partition("/")
    review = (
        target.kind == "document_review"
        and purpose in ("consultation_review", "commission_review", "act_application")
        and bool(source_key)
    )
    if (target.kind, target.ref) not in {
        ("bill", "joint"),
        ("editorial", "mnemonic"),
    } and not review:
        return observation
    if "bills" not in tables:
        return {**observation, "status": "table_unavailable"}
    exists = (
        connection.execute(
            "SELECT 1 FROM bills WHERE term=? AND number=?", (application.term, application.number)
        ).fetchone()
        is not None
    )
    return {**observation, "status": "owner_present" if exists else "historical_owner_absent"}

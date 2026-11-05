"""Audit exact paid receipt edges and retained batch item populations without owner inference."""

import sqlite3
from collections import Counter
from typing import Any

from lexinform.adapters.computation_history import read_computation_result
from lexinform.models import LlmBatch, LlmBatchItem
from lexinform.models.batch import batch_item_meta

_RESULT = {"result_id", "provider", "batch_id", "custom_id", "term", "number", "result_json"}
_BATCH = {
    "batch_id",
    "provider",
    "call_kind",
    "submitted_at",
    "status",
    "request_count",
    "estimated_cost_usd",
}
_ITEM = {"batch_id", "custom_id", "call_kind", "term", "number", "meta_json"}
_RECEIPT_COUNTS = (
    "rows",
    "valid_rows",
    "invalid_rows",
    "matched_rows",
    "missing_rows",
    "provider_mismatch_rows",
    "purpose_mismatch_rows",
    "sha_equal_rows",
    "sha_unknown_rows",
    "sha_mismatch_rows",
    "unassessed_rows",
    "item_rows",
    "invalid_item_rows",
)
_POPULATION_COUNTS = (
    "rows",
    "valid_rows",
    "invalid_rows",
    "assessed_rows",
    "equal_rows",
    "deficit_rows",
    "excess_rows",
    "unknown_rows",
    "item_rows",
)


def _inventory(
    connection: sqlite3.Connection, table: str, required: set[str]
) -> tuple[list[sqlite3.Row] | None, list[str]]:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        missing = sorted(
            required - {row["name"] for row in cursor.execute(f"PRAGMA table_info({table})")}
        )
        return (
            (None, missing)
            if missing
            else (
                cursor.execute(
                    f"SELECT rowid AS audit_id,* FROM {table} ORDER BY rowid"
                ).fetchall(),
                [],
            )
        )
    except sqlite3.Error:
        return None, sorted(required)
    finally:
        cursor.close()


def _issue(findings: list[dict[str, str]], code: str, ref: str, *, warning: bool = False) -> None:
    findings.append({"code": code, "ref": ref, "level": "warning" if warning else "error"})


def _section(
    counts: Counter[str], keys: tuple[str, ...], missing: dict[str, list[str]], *, rows: int | None
) -> dict[str, Any]:
    return {
        "status": "unavailable" if missing else "available",
        "missing_columns": missing,
        **{key: (rows if key == "rows" else None) if missing else counts[key] for key in keys},
    }


def _item(row: sqlite3.Row) -> LlmBatchItem:
    item = LlmBatchItem(
        batch_id=row["batch_id"],
        custom_id=row["custom_id"],
        call_kind=row["call_kind"],
        term=row["term"],
        number=row["number"],
        meta=batch_item_meta(row["call_kind"], row["meta_json"]),
    )
    if any(
        getattr(item, key) != row[key]
        for key in ("batch_id", "custom_id", "call_kind", "term", "number")
    ):
        raise ValueError("item_columns_mismatch")
    return item


def audit_receipts(
    connection: sqlite3.Connection, findings: list[dict[str, str]]
) -> dict[str, Any]:
    results, rm = _inventory(connection, "computation_results", _RESULT)
    batches, bm = _inventory(connection, "llm_batches", _BATCH)
    items, im = _inventory(connection, "llm_batch_items", _ITEM)
    missing = {
        table: columns
        for table, columns in (
            ("computation_results", rm),
            ("llm_batches", bm),
            ("llm_batch_items", im),
        )
        if columns
    }
    rc: Counter[str] = Counter()
    pc: Counter[str] = Counter()
    rc["rows"] = len(results or [])
    rc["item_rows"] = pc["item_rows"] = len(items or [])
    pc["rows"] = len(batches or [])
    batch_ids = {row["batch_id"] for row in batches or []}
    item_ids = {(row["batch_id"], row["custom_id"]) for row in items or []}
    valid_batches: dict[str, LlmBatch] = {}
    valid_items: dict[tuple[str, str], LlmBatchItem] = {}
    populations: Counter[str] = Counter()
    if items is not None:
        for row in items:
            populations[row["batch_id"]] += 1
            try:
                value = _item(row)
            except ValueError, TypeError, RecursionError, KeyError, IndexError:
                rc["invalid_item_rows"] += 1
                _issue(
                    findings, "invalid_paid_receipt_item", f"llm_batch_items/row-{row['audit_id']}"
                )
            else:
                valid_items[(value.batch_id, value.custom_id)] = value
    if batches is not None:
        for row in batches:
            ref = f"llm_batches/row-{row['audit_id']}"
            try:
                batch = LlmBatch.model_validate(dict(row))
                if (batch.batch_id, batch.provider) != (row["batch_id"], row["provider"]):
                    raise ValueError("batch_identity_mismatch")
                count = row["request_count"]
                if type(count) is not int or count < 0:
                    raise ValueError("invalid_expected_count")
            except ValueError, TypeError, RecursionError, KeyError, IndexError:
                pc["invalid_rows"] += 1
                _issue(findings, "invalid_batch_population", ref)
                continue
            valid_batches[batch.batch_id] = batch
            pc["valid_rows"] += 1
            if items is None:
                continue
            pc["assessed_rows"] += 1
            actual = populations[batch.batch_id]
            if actual == count:
                pc["equal_rows"] += 1
            elif actual < count:
                pc["deficit_rows"] += 1
                _issue(findings, "batch_item_deficit", ref, warning=True)
            else:
                pc["excess_rows"] += 1
                _issue(findings, "batch_item_excess", ref)
    if results is not None:
        for row in results:
            ref = f"computation_results/row-{row['audit_id']}"
            try:
                result = read_computation_result(row)
            except ValueError, TypeError, RecursionError, KeyError, IndexError:
                rc["invalid_rows"] += 1
                _issue(findings, "invalid_paid_receipt_result", ref)
                continue
            rc["valid_rows"] += 1
            if missing:
                continue
            receipt_batch = valid_batches.get(result.batch_id)
            item = valid_items.get((result.batch_id, result.custom_id))
            if receipt_batch is not None and receipt_batch.provider != result.provider:
                rc["provider_mismatch_rows"] += 1
                _issue(findings, "paid_receipt_provider_mismatch", ref)
            elif (
                receipt_batch is None
                and result.batch_id in batch_ids
                or item is None
                and (result.batch_id, result.custom_id) in item_ids
            ):
                rc["unassessed_rows"] += 1
            elif receipt_batch is None or item is None:
                rc["missing_rows"] += 1
                _issue(findings, "paid_receipt_missing", ref, warning=True)
            elif item.call_kind != result.purpose:
                rc["purpose_mismatch_rows"] += 1
                _issue(findings, "paid_receipt_purpose_mismatch", ref)
            else:
                rc["matched_rows"] += 1
                if result.request_sha256 is None or item.meta.request_sha256 is None:
                    rc["sha_unknown_rows"] += 1
                elif result.request_sha256 == item.meta.request_sha256:
                    rc["sha_equal_rows"] += 1
                else:
                    rc["sha_mismatch_rows"] += 1
                    _issue(findings, "paid_receipt_sha_mismatch", ref)
    pm = {
        table: columns
        for table, columns in (("llm_batches", bm), ("llm_batch_items", im))
        if columns
    }
    return {
        "paid_receipts": _section(
            rc, _RECEIPT_COUNTS, missing, rows=len(results) if results is not None else None
        ),
        "batch_populations": _section(
            pc, _POPULATION_COUNTS, pm, rows=len(batches) if batches is not None else None
        ),
    }

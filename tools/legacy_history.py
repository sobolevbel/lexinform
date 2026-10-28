"""Inventory exact saved answer values without assigning paid origin or modifying state."""

import argparse
import gzip
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from tools.corpus_artifacts import write_json
from tools.saved_records import (
    BILL_SNAPSHOT_FIELDS,
    CONTEXT_RESULT_TABLES,
    SavedRecordIssue,
    has_answer_payload,
    is_saved_answer,
    saved_columns,
    saved_records,
)
from tools.sqlite_snapshot import open_snapshot


def batch_receipts(
    connection: sqlite3.Connection,
    tables: set[str],
    counts: Counter[str],
    findings: list[dict[str, str]],
) -> None:
    if "llm_batch_items" in tables:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(llm_batch_items)")}
        if "result_json" in columns:
            for row in connection.execute(
                "SELECT rowid AS audit_id, result_json FROM llm_batch_items ORDER BY rowid"
            ):
                counts["batch_item_rows"] += 1
                if row["result_json"] is None:
                    counts["batch_result_absent"] += 1
                    continue
                try:
                    result = json.loads(row["result_json"])
                    if not isinstance(result, dict):
                        raise ValueError("a batch result must be an object")
                except ValueError, TypeError:
                    findings.append(
                        {
                            "code": "invalid_batch_result_json",
                            "ref": f"llm_batch_items/{row['audit_id']}",
                        }
                    )
                    continue
                counts[
                    "batch_answer_present" if has_answer_payload(result) else "batch_receipt_only"
                ] += 1


def audit(database: Path) -> dict[str, Any]:
    counts: Counter[str] = Counter(
        record_occurrences=0,
        canonical_occurrence_bytes=0,
        recorded_ref_occurrences=0,
        unbound_occurrences=0,
    )
    records: dict[str, dict[str, Any]] = {}
    payloads: dict[str, bytes] = {}
    findings: list[dict[str, str]] = []
    with open_snapshot(database) as snapshot:
        connection = snapshot.connection
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for column in saved_columns(connection, tables, catalogue=CONTEXT_RESULT_TABLES):
            counts["saved_columns"] += 1
            if not isinstance(column.raw, str):
                findings.append({"code": "invalid_json", "ref": column.ref})
                continue
            try:
                value = json.loads(column.raw)
            except ValueError, RecursionError:
                findings.append({"code": "invalid_json", "ref": column.ref})
                continue
            for item in saved_records(value, column.ref, encoded_fields=BILL_SNAPSHOT_FIELDS):
                if isinstance(item, SavedRecordIssue):
                    findings.append({"code": item.code, "ref": item.ref})
                    continue
                if not is_saved_answer(item.value):
                    continue
                try:
                    encoded = json.dumps(
                        item.value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode()
                except ValueError, RecursionError:
                    findings.append({"code": "invalid_record_json", "ref": item.ref})
                    continue
                identifier = hashlib.sha256(encoded).hexdigest()
                payloads[identifier] = encoded
                record = records.setdefault(
                    identifier,
                    {
                        "record_sha256": identifier,
                        "canonical_bytes": len(encoded),
                        "has_recorded_result_ref": item.value.get("computation_id") is not None,
                        "has_answer_payload": has_answer_payload(item.value),
                        "sources": [],
                    },
                )
                record["sources"].append(item.ref)
                counts["record_occurrences"] += 1
                counts["canonical_occurrence_bytes"] += len(encoded)
                counts[
                    "recorded_ref_occurrences"
                    if record["has_recorded_result_ref"]
                    else "unbound_occurrences"
                ] += 1
        batch_receipts(connection, tables, counts, findings)
        if not snapshot.unchanged():
            findings.append({"code": "database_changed_during_audit", "ref": "database"})
    ordered = sorted(payloads)
    framed = b"[" + b",".join(payloads[key] for key in ordered) + b"]"
    counts["unique_records"] = len(records)
    counts["duplicate_occurrences"] = counts["record_occurrences"] - len(records)
    counts["unique_canonical_bytes"] = sum(len(payloads[key]) for key in ordered)
    findings.sort(key=lambda row: (row["code"], row["ref"]))
    return {
        "version": 1,
        "status": "failed"
        if findings
        else "recorded"
        if records or counts["batch_item_rows"]
        else "not_run",
        "database_sha256": snapshot.sha256,
        "missing_source_tables": sorted(set(CONTEXT_RESULT_TABLES) - tables),
        "summary": dict(sorted(counts.items())),
        "unique_json_array_bytes": len(framed),
        "unique_json_array_gzip_bytes": len(gzip.compress(framed, mtime=0)),
        "records": [records[key] for key in ordered],
        "findings": findings,
        "scope": "Saved JSON values and receipt shapes; no model defaults or paid origin inferred.",
        "exclusions": [
            "Saved answer shapes and recorded refs do not prove paid origin or legal validity.",
            "Memo keys are not joined to batch receipts to invent result identities.",
            "No raw input, historical source bytes or missing request is reconstructed.",
            "Canonical sizes are a measurement, not a selected production storage policy.",
            "JSON key order is canonical; strings and stored fields stay unchanged.",
            "No answer content is emitted, imported, archived or removed.",
        ],
    }


def run(database: Path, output: Path) -> int:
    if output.resolve() == database.resolve():
        raise ValueError("Report must be separate from the input database")
    report = audit(database)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    return 1 if report["status"] == "failed" else 2 if report["status"] == "not_run" else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        code = run(args.database, args.output)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))
    raise SystemExit(code)


if __name__ == "__main__":
    main()

"""Check saved computation/application identities and result references without executing work."""

import argparse
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lexinform.models import ComputationResult, ResultApplication
from tools.corpus_artifacts import write_json
from tools.saved_records import (
    RESULT_TABLES,
    SavedRecordIssue,
    SavedTable,
    is_saved_answer,
    saved_columns,
    saved_records,
)
from tools.sqlite_snapshot import open_snapshot

_RESULT_ID = re.compile(r"result-v1:[0-9a-f]{64}")
_TABLES = {
    **RESULT_TABLES,
    "bills": SavedTable(
        RESULT_TABLES["bills"].keys, (*RESULT_TABLES["bills"].columns, "review_work_json")
    ),
    "document_work": SavedTable(("key",), ("work_json",)),
    "llm_batch_items": SavedTable(("batch_id", "custom_id"), ("meta_json",)),
    "llm_batch_intents": SavedTable(("custom_id",), ("meta_json",)),
}
_ENCODED = frozenset({"bill_json", "change_json", "input_bill_json", "source_bill_json"})
_REQUIRED = {*RESULT_TABLES, "computation_results", "result_applications"}


@dataclass(frozen=True)
class ResultIdentity:
    has_record: bool
    request_sha256: str | None


@dataclass
class ComputationAudit:
    counts: Counter[str] = field(default_factory=Counter)
    findings: list[dict[str, str]] = field(default_factory=list)
    results: dict[str, ResultIdentity] = field(default_factory=dict)
    refs: dict[str, list[str]] = field(default_factory=dict)
    application_targets: Counter[str] = field(default_factory=Counter)

    def issue(self, code: str, ref: str) -> None:
        self.findings.append({"code": code, "ref": ref, "level": "error"})

    def computations(self, connection: sqlite3.Connection) -> None:
        for row in connection.execute("SELECT * FROM computation_results ORDER BY result_id"):
            ref = f"computation_results/{row['result_id']}"
            self.counts["result_rows"] += 1
            try:
                result = ComputationResult.model_validate_json(row["result_json"])
            except ValueError:
                self.issue("invalid_computation_result", ref)
                continue
            expected = (
                result.result_id,
                result.provider,
                result.batch_id,
                result.custom_id,
                result.term,
                result.number,
            )
            observed = tuple(
                row[key]
                for key in ("result_id", "provider", "batch_id", "custom_id", "term", "number")
            )
            if observed != expected:
                self.issue("computation_columns_mismatch", ref)
            self.results[row["result_id"]] = ResultIdentity(
                result.record is not None, result.request_sha256
            )
            self.counts[f"result_{result.validation}"] += 1
            self.counts[f"purpose_{result.purpose}"] += 1
            self.counts[
                "input_binding_known" if result.input_binding else "input_binding_unknown"
            ] += 1
            self.counts[
                "request_sha_known" if result.request_sha256 else "request_sha_unknown"
            ] += 1

    def applications(self, connection: sqlite3.Connection) -> None:
        for row in connection.execute(
            "SELECT rowid AS audit_id, * FROM result_applications ORDER BY rowid"
        ):
            ref = f"result_applications/{row['audit_id']}"
            self.counts["application_rows"] += 1
            try:
                application = ResultApplication.model_validate_json(row["application_json"])
            except ValueError:
                self.issue("invalid_result_application", ref)
                continue
            expected = (
                application.result_id,
                application.term,
                application.number,
                application.generation,
                application.phase,
            )
            observed = tuple(
                row[key] for key in ("result_id", "term", "number", "generation", "phase")
            )
            if expected != observed:
                self.issue("application_columns_mismatch", ref)
            result = self.results.get(application.result_id)
            if result is None:
                self.issue("application_result_unavailable", ref)
            elif application.phase == "projection" and not result.has_record:
                self.issue("failed_result_applied", ref)
            elif (application.outcome == "ready" and not result.has_record) or (
                application.outcome == "failed" and result.has_record
            ):
                self.issue("collection_result_mismatch", ref)
            self.refs.setdefault(application.result_id, []).append(ref)
            self.counts[f"application_{application.phase}"] += 1
            self.counts[f"application_outcome_{application.outcome}"] += 1
            if application.target is not None:
                self.application_targets[application.target.kind] += 1

    def projections(self, connection: sqlite3.Connection, tables: set[str]) -> None:
        for column in saved_columns(connection, tables, catalogue=_TABLES):
            self.counts["saved_columns"] += 1
            if not isinstance(column.raw, str):
                self.issue("invalid_json", column.ref)
                continue
            try:
                value = json.loads(column.raw)
            except ValueError, RecursionError:
                self.issue("invalid_json", column.ref)
                continue
            for item in saved_records(value, column.ref, encoded_fields=_ENCODED):
                if isinstance(item, SavedRecordIssue):
                    self.issue(item.code, item.ref)
                elif "computation_id" in item.value or is_saved_answer(item.value):
                    self.reference(item.value, item.ref)

    def reference(self, value: dict[str, Any], ref: str) -> None:
        identity = value.get("computation_id")
        if identity is None:
            self.counts["projection_refs_unknown"] += 1
            return
        self.counts["projection_refs_known"] += 1
        if not isinstance(identity, str) or _RESULT_ID.fullmatch(identity) is None:
            self.issue("invalid_computation_reference", ref)
            return
        self.refs.setdefault(identity, []).append(ref)
        result = self.results.get(identity)
        if result is None:
            self.issue("projection_result_unavailable", ref)
        elif not result.has_record:
            self.issue("failed_result_projected", ref)
        else:
            recorded = value.get("request_sha256")
            if (
                recorded is not None
                and result.request_sha256 is not None
                and recorded != result.request_sha256
            ):
                self.issue("projection_request_mismatch", ref)


def audit(database: Path) -> dict[str, Any]:
    result = ComputationAudit()
    with open_snapshot(database) as snapshot:
        connection = snapshot.connection
        schema = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = sorted(_REQUIRED - tables)
        if not missing:
            result.computations(connection)
            result.applications(connection)
            result.projections(connection, tables)
        if not snapshot.unchanged():
            result.issue("database_changed_during_audit", "database")
    result.findings.sort(key=lambda item: (item["code"], item["ref"]))
    result.counts["errors"] = len(result.findings)
    observed = sum(
        result.counts[key]
        for key in (
            "result_rows",
            "application_rows",
            "projection_refs_known",
            "projection_refs_unknown",
        )
    )
    status = "failed" if result.findings else "not_run" if missing or not observed else "recorded"
    return {
        "version": 1,
        "status": status,
        "database_sha256": snapshot.sha256,
        "schema_version": schema,
        "missing_tables": missing,
        "summary": dict(sorted(result.counts.items())),
        "findings": result.findings,
        "references": {key: sorted(refs) for key, refs in sorted(result.refs.items())},
        "application_targets": dict(sorted(result.application_targets.items())),
        "scope": "Recorded computation/application identities and saved result references.",
        "exclusions": [
            "Unknown legacy IDs, keys and request checksums are not reconstructed.",
            "Input scope, collection owner and application target may legitimately differ.",
            "Current generation/status is not compared with immutable historical receipts.",
            "Application target kinds/refs remain opaque; no target is executed or reinterpreted.",
            "No blob coverage, legal applicability, billing or full-state health verdict.",
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

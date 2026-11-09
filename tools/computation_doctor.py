"""Check saved computation/application identities and result references without executing work."""

import argparse
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lexinform.adapters.amendment_inputs import amendment_input_issues
from lexinform.adapters.computation_history import parse_computation_result
from lexinform.adapters.content_source_refs import is_primary_projection_result
from lexinform.adapters.input_receipts import input_snapshot_checks
from lexinform.adapters.primary_projections import primary_projection_issues
from lexinform.adapters.review_memos import review_memo_issues
from lexinform.adapters.secondary_dependencies import secondary_dependency_issues
from lexinform.adapters.supplement_deliveries import supplement_delivery_links
from lexinform.adapters.supplement_dependencies import supplement_dependency_issues
from lexinform.models import (
    AmendmentsRecord,
    AnalysisRecord,
    ComputationResult,
    ResultApplicabilityObservation,
    ResultApplication,
    SupplementRecord,
)
from lexinform.models.batch import BatchItemMeta, batch_item_meta
from lexinform.models.computations import ApplicationReason
from tools.application_targets import application_target
from tools.corpus_artifacts import write_json
from tools.saved_records import (
    BILL_SNAPSHOT_FIELDS,
    CONTEXT_RESULT_TABLES,
    RESULT_TABLES,
    SavedRecordIssue,
    is_saved_answer,
    saved_columns,
    saved_records,
)
from tools.sqlite_snapshot import open_snapshot, snapshot_files

_RESULT_ID = re.compile(r"result-v1:[0-9a-f]{64}")
_REQUIRED = {*RESULT_TABLES, "llm_batches", "computation_results", "result_applications"}


@dataclass(frozen=True)
class ResultIdentity:
    has_record: bool
    request_sha256: str | None
    input_binding_known: bool
    columns_match: bool
    primary_projection: bool


@dataclass
class ProjectionReceiptCensus:
    collection: int = 0
    invalid: int = 0
    projections: Counter[tuple[ApplicationReason, bool]] = field(default_factory=Counter)

    def report(self) -> dict[str, Any]:
        return {
            "application_rows": self.collection + self.invalid + self.projections.total(),
            "collection_rows": self.collection,
            "invalid_rows": self.invalid,
            "projections_by_reason": {
                reason: {
                    "input_binding_known": self.projections[reason, True],
                    "input_binding_unknown": self.projections[reason, False],
                }
                for reason in ("batch_result", "memo_reuse", "retained_result")
            },
        }


@dataclass
class ComputationAudit:
    counts: Counter[str] = field(default_factory=Counter)
    findings: list[dict[str, str]] = field(default_factory=list)
    results: dict[str, ResultIdentity] = field(default_factory=dict)
    refs: dict[str, list[str]] = field(default_factory=dict)
    application_targets: Counter[str] = field(default_factory=Counter)
    target_links: list[dict[str, Any]] = field(default_factory=list)
    receipt_census: ProjectionReceiptCensus | None = None

    def issue(self, code: str, ref: str, *, level: str = "error") -> None:
        self.findings.append({"code": code, "ref": ref, "level": level})

    def computations(self, connection: sqlite3.Connection) -> None:
        for row in connection.execute(
            """
            SELECT r.*, i.call_kind AS item_kind, i.term AS item_term, i.number AS item_number,
                i.meta_json AS item_meta, i.consumed_at AS item_consumed_at,
                b.provider AS item_provider
            FROM computation_results r
            LEFT JOIN llm_batch_items i ON i.batch_id=r.batch_id AND i.custom_id=r.custom_id
            LEFT JOIN llm_batches b ON b.batch_id=i.batch_id
            ORDER BY r.result_id
            """
        ):
            ref = f"computation_results/{row['result_id']}"
            self.counts["result_rows"] += 1
            try:
                result = parse_computation_result(row["result_json"])
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
            columns_match = observed == expected
            if not columns_match:
                self.issue("computation_columns_mismatch", ref)
            self.results[row["result_id"]] = ResultIdentity(
                result.record is not None,
                result.request_sha256,
                result.input_binding is not None,
                columns_match,
                columns_match and is_primary_projection_result(result),
            )
            if columns_match and result.validation == "validated":
                self.retained_scan(row, result, ref)
            self.counts[f"result_{result.validation}"] += 1
            self.counts[f"purpose_{result.purpose}"] += 1
            self.counts[
                "input_binding_known" if result.input_binding else "input_binding_unknown"
            ] += 1
            self.counts[
                "request_sha_known" if result.request_sha256 else "request_sha_unknown"
            ] += 1

    def retained_scan(self, item: sqlite3.Row, result: ComputationResult, ref: str) -> None:
        record = result.record
        if isinstance(record, AnalysisRecord):
            scan = record.selection.scan_input
        elif isinstance(record, (AmendmentsRecord, SupplementRecord)):
            scan = record.scan_input
        else:
            return

        if item["item_provider"] is None:
            if scan is not None:
                self.counts["retained_scan_unverified"] += 1
            return
        try:
            meta = batch_item_meta(item["item_kind"], item["item_meta"])
        except ValueError:
            if scan is not None:
                self.issue("invalid_retained_scan_item_metadata", ref)
            return
        submitted = (
            meta.selection.scan_input if isinstance(meta, BatchItemMeta) else meta.scan_input
        )
        if scan is None and submitted is None:
            return
        if tuple(
            item[key] for key in ("item_provider", "item_kind", "item_term", "item_number")
        ) != (
            result.provider,
            result.purpose,
            result.term,
            result.number,
        ):
            self.issue("retained_scan_item_identity_mismatch", ref)
            return
        if (
            meta.request_sha256 is not None
            and result.request_sha256 is not None
            and meta.request_sha256 != result.request_sha256
        ):
            self.issue("retained_scan_request_mismatch", ref)
            return
        manifest = meta.frozen_request_manifest
        if manifest is not None and manifest.provider != result.provider:
            self.issue("retained_scan_provider_mismatch", ref)
        elif scan is not None and submitted is not None and submitted != scan:
            self.issue("retained_scan_manifest_mismatch", ref)
        elif (
            scan is None
            or submitted is None
            or manifest is None
            or not result.request_sha256
            or not item["item_consumed_at"]
        ):
            self.counts["retained_scan_unverified"] += 1
            if scan is None:
                self.counts["retained_scan_result_manifest_unavailable"] += 1
                self.issue("retained_scan_result_manifest_unavailable", ref, level="warning")
        else:
            self.counts["retained_scan_bound"] += 1

    def applications(self, connection: sqlite3.Connection, tables: set[str]) -> None:
        census = self.receipt_census
        for row in connection.execute(
            "SELECT rowid AS audit_id, * FROM result_applications ORDER BY rowid"
        ):
            ref = f"result_applications/{row['audit_id']}"
            self.counts["application_rows"] += 1
            findings_before = len(self.findings)
            try:
                application = ResultApplication.model_validate_json(row["application_json"])
            except ValueError:
                self.issue("invalid_result_application", ref)
                if census is not None:
                    census.invalid += 1
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
            if (
                result is not None
                and result.has_record
                and application.projected_generation is not None
                and not result.primary_projection
            ):
                self.issue("invalid_primary_projection_witness", ref)
            if census is not None:
                if (
                    result is None
                    or not result.columns_match
                    or len(self.findings) != findings_before
                ):
                    census.invalid += 1
                elif application.phase == "collection":
                    census.collection += 1
                else:
                    census.projections[application.reason, result.input_binding_known] += 1
            self.refs.setdefault(application.result_id, []).append(ref)
            self.counts[f"application_{application.phase}"] += 1
            self.counts[f"application_outcome_{application.outcome}"] += 1
            if application.target is not None:
                self.application_targets[application.target.kind] += 1
            target = application_target(connection, application, tables)
            if target is not None:
                self.target_links.append({"application": ref, **target})
                self.counts[f"target_{target['status']}"] += 1
                if target["status"] in ("row_unavailable", "table_unavailable"):
                    self.issue("application_target_unavailable", ref, level="warning")

    def applicability(self, connection: sqlite3.Connection) -> None:
        required = {"observation_id", "result_id", "term", "number", "observation_json"}
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(result_applicability)")
        }
        self.counts["applicability_rows"] = 0
        if not required <= columns:
            self.issue("applicability_columns_unavailable", "result_applicability")
            return
        for row in connection.execute(
            "SELECT rowid AS audit_id, * FROM result_applicability ORDER BY rowid"
        ):
            ref = f"result_applicability/{row['audit_id']}"
            self.counts["applicability_rows"] += 1
            try:
                observation = ResultApplicabilityObservation.model_validate_json(
                    row["observation_json"]
                )
            except ValueError:
                self.issue("invalid_applicability_observation", ref)
                continue
            expected = (
                observation.observation_id,
                observation.result_id,
                observation.term,
                observation.number,
            )
            observed = tuple(row[key] for key in ("observation_id", "result_id", "term", "number"))
            if observed != expected:
                self.issue("applicability_columns_mismatch", ref)
            result = self.results.get(observation.result_id)
            if result is None:
                self.issue("applicability_result_unavailable", ref)
            elif not result.has_record:
                self.issue("failed_result_associated", ref)
            self.refs.setdefault(observation.result_id, []).append(ref)

    def projections(self, connection: sqlite3.Connection, tables: set[str]) -> None:
        for column in saved_columns(connection, tables, catalogue=CONTEXT_RESULT_TABLES):
            self.counts["saved_columns"] += 1
            if not isinstance(column.raw, str):
                self.issue("invalid_json", column.ref)
                continue
            try:
                value = json.loads(column.raw)
            except ValueError, RecursionError:
                self.issue("invalid_json", column.ref)
                continue
            for item in saved_records(value, column.ref, encoded_fields=BILL_SNAPSHOT_FIELDS):
                if isinstance(item, SavedRecordIssue):
                    self.issue(item.code, item.ref)
                elif "modern_primary_ref" in item.value:
                    self.counts["modern_primary_projection_refs"] += 1
                    self.reference(
                        {
                            "computation_id": item.value["modern_primary_ref"],
                            "request_sha256": item.value.get("request_sha256"),
                        },
                        item.ref,
                    )
                elif "modern_review_ref" in item.value:
                    self.reference(
                        {
                            "computation_id": item.value["modern_review_ref"],
                            "request_sha256": item.value.get("request_sha256"),
                        },
                        item.ref,
                    )
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


def audit(
    database: Path,
    *,
    include_supplement_deliveries: bool = False,
    include_projection_receipts: bool = False,
) -> dict[str, Any]:
    census = ProjectionReceiptCensus() if include_projection_receipts else None
    result = ComputationAudit(receipt_census=census)
    deliveries: dict[str, Any] | None = None
    with open_snapshot(database) as snapshot:
        connection = snapshot.connection
        schema = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = sorted(_REQUIRED - tables)
        for check in input_snapshot_checks(connection):
            result.counts["input_snapshot_rows"] += 1
            result.counts["input_receipts"] += check.receipt
            result.counts["frozen_requests"] += check.frozen_request
            if check.code is not None:
                result.issue(check.code, check.ref)
        if not missing:
            result.computations(connection)
            result.applications(connection, tables)
            if "result_applicability" in tables:
                result.applicability(connection)
            elif schema >= 51:
                result.issue("applicability_table_unavailable", "result_applicability")
            result.projections(connection, tables)
            for code, ref in review_memo_issues(connection):
                result.issue(code, ref)
            for code, ref in amendment_input_issues(connection):
                result.issue(code, ref)
            for code, ref in primary_projection_issues(connection):
                result.issue(code, ref)
            for code, ref in supplement_dependency_issues(connection):
                result.issue(code, ref)
            for code, ref in secondary_dependency_issues(connection):
                result.issue(code, ref)
        if include_supplement_deliveries:
            deliveries = supplement_delivery_links(connection)
        if not snapshot.unchanged():
            result.issue("database_changed_during_audit", "database")
    result.findings.sort(key=lambda item: (item["code"], item["ref"]))
    result.counts["errors"] = sum(item["level"] == "error" for item in result.findings)
    result.counts["warnings"] = sum(item["level"] == "warning" for item in result.findings)
    observed = sum(
        result.counts[key]
        for key in (
            "result_rows",
            "application_rows",
            "applicability_rows",
            "projection_refs_known",
            "projection_refs_unknown",
            "input_snapshot_rows",
        )
    )
    status = (
        "failed"
        if result.counts["errors"]
        else "not_run"
        if missing or not observed
        else "recorded"
    )
    report = {
        "version": 1,
        "status": status,
        "database_sha256": snapshot.sha256,
        "schema_version": schema,
        "missing_tables": missing,
        "summary": dict(sorted(result.counts.items())),
        "findings": result.findings,
        "references": {key: sorted(refs) for key, refs in sorted(result.refs.items())},
        "application_targets": dict(sorted(result.application_targets.items())),
        "target_links": result.target_links,
        "scope": "Recorded computation/application identities and saved result references.",
        "exclusions": [
            "Unknown legacy IDs, keys and request checksums are not reconstructed.",
            "Input scope, collection owner and application target may legitimately differ.",
            "Current generation/status is not compared with immutable historical receipts.",
            "Unsupported target kinds/refs remain opaque; no target is executed.",
            "Target presence does not revalidate historical application or present slot contents.",
            "Source associations do not revalidate current owners or source URLs.",
            "No blob coverage, legal applicability, billing or full-state health verdict.",
        ],
    }

    if include_supplement_deliveries:
        report["supplement_deliveries"] = deliveries
    if census is not None:
        report["projection_receipt_census"] = {
            "status": "schema_unavailable" if missing else "recorded",
            "counts": None if missing else census.report(),
            "scope": "Saved application receipts; not cache lookups, hit rate or billing.",
        }
    return report


def run(
    database: Path,
    output: Path,
    *,
    include_supplement_deliveries: bool = False,
    include_projection_receipts: bool = False,
) -> int:
    if output.resolve() in snapshot_files(database):
        raise ValueError("Report must be separate from the input database")
    report = audit(
        database,
        include_supplement_deliveries=include_supplement_deliveries,
        include_projection_receipts=include_projection_receipts,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    return 1 if report["status"] == "failed" else 2 if report["status"] == "not_run" else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--supplement-deliveries", action="store_true")
    parser.add_argument("--projection-receipts", action="store_true")
    args = parser.parse_args()
    try:
        code = run(
            args.database,
            args.output,
            include_supplement_deliveries=args.supplement_deliveries,
            include_projection_receipts=args.projection_receipts,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))
    raise SystemExit(code)


if __name__ == "__main__":
    main()

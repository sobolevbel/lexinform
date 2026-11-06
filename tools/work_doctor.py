"""Inspect unfinished document and review owners in a checkpointed SQLite copy."""

import argparse
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lexinform.adapters.document_work import (
    DocumentWorkRowError,
    read_document_work,
    revision_handoff_checks,
)
from lexinform.adapters.input_receipts import parse_batch_request
from lexinform.models import AmendmentRevisionApplicationWork, ReviewWork
from lexinform.models.documents import DocumentDeferReason
from lexinform.saved_clock import saved_clock_age
from tools.corpus_artifacts import write_json
from tools.observed_latency import observed_latency
from tools.observed_timing import observed_timing
from tools.sqlite_snapshot import open_snapshot

_REQUIRED = {"bills", "document_work", "status_changes", "publications", "analysis_memo"}


def _age_group(
    items: list[dict[str, Any]], observed_at: datetime, field_name: str
) -> dict[str, Any]:
    ages: list[tuple[int, str]] = []
    unknown: Counter[str] = Counter()
    for owner in items:
        raw = owner.get(field_name)
        age = saved_clock_age(raw if isinstance(raw, str) else None, observed_at)
        if age.unknown is not None:
            unknown[age.unknown] += 1
        elif age.seconds is not None:
            ages.append((age.seconds, owner["ref"]))
    oldest = max(ages, key=lambda item: item[0]) if ages else None
    return {
        "count": len(items),
        "timestamp_field": field_name,
        "known_ages": len(ages),
        "unknown_ages": dict(sorted(unknown.items())),
        "oldest_seconds": oldest[0] if oldest is not None else None,
        "oldest_ref": oldest[1] if oldest is not None else None,
    }


def work_ages(
    owners: list[dict[str, Any]], observed_at: datetime, *, complete: bool
) -> dict[str, Any]:
    """Age saved nonterminal owners from their recorded work timestamp, never a source date."""
    if observed_at.utcoffset() is None:
        raise ValueError("Observed time must include a timezone")
    try:
        observed_iso = observed_at.astimezone(UTC).isoformat()
    except OverflowError:
        observed_iso = observed_at.isoformat()
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    excluded: Counter[str] = Counter()
    for owner in owners:
        kind, state = owner["kind"], owner["state"]
        nonterminal = kind in ("llm_intent", "llm_item") or (kind, state) in (
            ("document", "waiting"),
            ("revision_application", "waiting"),
            ("revision_application", "blocked"),
            ("review", "context"),
            ("review", "snapshot"),
        )
        if nonterminal:
            groups.setdefault((kind, state), []).append(owner)
        else:
            excluded[f"{kind}/{state}"] += 1
    records: list[dict[str, Any]] = []
    for (kind, state), items in sorted(groups.items()):
        field_name = (
            "since" if kind == "review" else "submitted_at" if kind == "llm_item" else "created_at"
        )
        records.append(
            {
                "kind": kind,
                "state": state,
                **_age_group(items, observed_at, field_name),
            }
        )
    return {
        "observed_at": observed_iso,
        "coverage": "recorded" if complete else "partial",
        "nonterminal_owners": sum(row["count"] for row in records),
        "groups": records,
        "excluded_terminal_owners": dict(sorted(excluded.items())),
        "scope": (
            "Saved work age in whole seconds; runnable state, enabled flags "
            "and source/delivery latency are not evaluated."
        ),
    }


_LLM_COLUMNS = {
    "llm_batch_intents": {"custom_id", "provider", "request_json", "state", "created_at"},
    "llm_batch_items": {"batch_id", "custom_id", "call_kind", "term", "number", "consumed_at"},
    "llm_batches": {"batch_id", "provider", "status", "submitted_at"},
}


def llm_work_ages(connection: sqlite3.Connection, observed_at: datetime) -> dict[str, Any]:
    """Count saved submission intents and physical unconsumed provider items once each."""
    missing: list[str] = []
    for table, required in _LLM_COLUMNS.items():
        present = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        missing.extend(f"{table}.{name}" for name in sorted(required - present))
    owners: list[dict[str, Any]] = []
    submitted: dict[str, list[sqlite3.Row]] = {}
    conflicts: list[str] = []
    unknown_items: list[str] = []
    suppressed = 0
    item_schema = not any(name.startswith(("llm_batch_items.", "llm_batches.")) for name in missing)
    if item_schema:
        for row in connection.execute(
            "SELECT i.*, b.provider, b.status, b.submitted_at FROM llm_batch_items i "
            "LEFT JOIN llm_batches b ON b.batch_id=i.batch_id "
            "WHERE i.consumed_at IS NULL ORDER BY i.batch_id, i.custom_id"
        ):
            submitted.setdefault(row["custom_id"], []).append(row)
            if row["status"] not in ("submitted", "ended", "failed"):
                unknown_items.append(f"llm_batch_items/{row['batch_id']}/{row['custom_id']}")
            owners.append(
                {
                    "kind": "llm_item",
                    "state": row["status"] or "unknown",
                    "ref": f"llm_batch_items/{row['batch_id']}/{row['custom_id']}",
                    "submitted_at": row["submitted_at"],
                }
            )
    if not any(name.startswith("llm_batch_intents.") for name in missing):
        for row in connection.execute(
            "SELECT * FROM llm_batch_intents WHERE state IN ('queued', 'submitting') "
            "ORDER BY custom_id"
        ):
            ref = f"llm_batch_intents/{row['custom_id']}"
            try:
                request = parse_batch_request(row["request_json"])
            except ValueError, TypeError, KeyError:
                request = None
            candidates = submitted.get(row["custom_id"], [])
            exact = (
                bool(candidates)
                and request is not None
                and all(
                    (request.term, request.number, request.call_kind)
                    == (item["term"], item["number"], item["call_kind"])
                    and request.custom_id == row["custom_id"]
                    and row["provider"] == item["provider"]
                    for item in candidates
                )
            )
            if exact:
                suppressed += 1
                continue
            if candidates:
                conflicts.append(ref)
            owners.append(
                {
                    "kind": "llm_intent",
                    "state": row["state"],
                    "ref": ref,
                    "created_at": row["created_at"],
                }
            )
    report = work_ages(
        owners, observed_at, complete=not missing and not conflicts and not unknown_items
    )
    report["nonterminal_work_items"] = report.pop("nonterminal_owners")
    report["missing_schema"] = missing
    report["unknown_provider_items"] = unknown_items
    report["suppressed_submitted_intents"] = suppressed
    report["conflicting_or_unknown_intent_owners"] = conflicts
    for group in report["groups"]:
        group["phase"] = (
            "waiting_submission"
            if group["kind"] == "llm_intent"
            else "waiting_provider"
            if group["state"] == "submitted"
            else "waiting_local_consumption"
            if group["state"] in ("ended", "failed")
            else "unknown"
        )
    report["scope"] = (
        "Intent age is since saved creation; item age is total age since saved submission. "
        "Provider finish and local-consumption phase start are unknown; polling is not completion. "
        "Items count by batch_id/custom_id, including distinct retry submissions."
    )
    return report


@dataclass
class WorkAudit:
    counts: Counter[str] = field(default_factory=Counter)
    findings: list[dict[str, str]] = field(default_factory=list)
    owners: list[dict[str, Any]] = field(default_factory=list)
    document_defer_reasons: dict[str, DocumentDeferReason] = field(default_factory=dict)

    def issue(self, code: str, ref: str, *, level: str = "error") -> None:
        self.findings.append({"code": code, "ref": ref, "level": level})

    def documents(self, connection: sqlite3.Connection) -> None:
        for ref, code in revision_handoff_checks(connection):
            self.counts["revision_application_handoffs"] += 1
            if code is not None:
                self.issue(code, ref)
        for row in connection.execute("SELECT * FROM document_work ORDER BY key"):
            ref = f"document_work/{row['key']}"
            self.counts["document_rows"] += 1
            try:
                work = read_document_work(connection, row)
            except DocumentWorkRowError as exc:
                self.issue(str(exc), ref)
                continue
            if isinstance(work, AmendmentRevisionApplicationWork):
                self.revision_application(connection, ref, work)
                continue
            for name in ("bill_json", "detail_json", "stage_json", "document_json", "context_json"):
                self.snapshot(getattr(work, name), f"{ref}/{name}")
            self.counts[f"document_{work.state}"] += 1
            bill = connection.execute(
                "SELECT 1 FROM bills WHERE term=? AND number=?", (work.term, work.number)
            ).fetchone()
            change = connection.execute(
                "SELECT term, number FROM status_changes WHERE id=?", (work.change_id,)
            ).fetchone()
            if bill is None:
                self.issue("missing_document_bill", ref)
            if change is None:
                self.issue("missing_document_change", ref)
            elif (change["term"], change["number"]) != (work.term, work.number):
                self.issue("document_change_owner_mismatch", ref)
            publications = connection.execute(
                "SELECT status, delivery_json IS NOT NULL AS payload FROM publications "
                "WHERE status_change_id=? ORDER BY id",
                (work.change_id,),
            ).fetchall()
            if work.state != "complete" and not publications:
                self.issue("document_publication_unavailable", ref, level="warning")
            if work.state == "waiting" and work.last_defer_reason is not None:
                self.document_defer_reasons[ref] = work.last_defer_reason
            self.owners.append(
                {
                    "ref": ref,
                    "kind": "document",
                    "term": work.term,
                    "number": work.number,
                    "state": work.state,
                    "change_id": work.change_id,
                    "attempts": work.attempts,
                    "created_at": work.created_at.isoformat(),
                    "next_at": work.next_at.isoformat(),
                    "frozen_document": work.document_json is not None,
                    "frozen_context": work.context_json is not None,
                    "linked_publications": dict(
                        sorted(Counter(pub["status"] for pub in publications).items())
                    ),
                    "retained_delivery_payloads": sum(pub["payload"] for pub in publications),
                    "protected_memo_rows": self.memos(connection, work.term, work.number)
                    if work.state != "complete"
                    else 0,
                }
            )

    def document_wait_ages_by_reason(self, observed_at: datetime) -> dict[str, Any]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for owner in self.owners:
            if owner["kind"] == "document" and owner["state"] == "waiting":
                reason = self.document_defer_reasons.get(owner["ref"])
                groups.setdefault(reason or "unknown", []).append(owner)
        return {
            "groups": {
                reason: _age_group(items, observed_at, "created_at")
                for reason, items in sorted(groups.items())
            },
            "scope": "Age since saved work creation, grouped by historical deferral reason.",
        }

    def revision_application(
        self, connection: sqlite3.Connection, ref: str, work: AmendmentRevisionApplicationWork
    ) -> None:
        self.counts["revision_application_rows"] += 1
        self.counts[f"revision_application_{work.state}"] += 1
        original = connection.execute(
            "SELECT delivery_json IS NOT NULL AS retained, EXISTS (SELECT 1"
            " FROM publication_content WHERE publication_id=publications.id) AS bound"
            " FROM publications WHERE id=?",
            (work.original_publication_id,),
        ).fetchone()
        witness = (
            "content_version"
            if original is not None and original["bound"]
            else "delivery"
            if original is not None and original["retained"]
            else "unknown"
        )
        self.owners.append(
            {
                "ref": ref,
                "kind": "revision_application",
                "term": work.term,
                "number": work.number,
                "state": work.state,
                "attempts": work.attempts,
                "created_at": work.created_at.isoformat(),
                "next_at": work.next_at.isoformat(),
                "result_id": work.result_id,
                "original_publication_id": work.original_publication_id,
                "revision_publication_id": work.revision_publication_id,
                "original_context_witness": witness,
                "protected_memo_rows": self.memos(connection, work.term, work.number)
                if work.state != "complete"
                else 0,
            }
        )

    def reviews(self, connection: sqlite3.Connection) -> None:
        for row in connection.execute(
            "SELECT term, number, review_work_json FROM bills WHERE review_work_json IS NOT NULL "
            "ORDER BY term, number"
        ):
            ref = f"bills/{row['term']}/{row['number']}/review_work"
            self.counts["review_owner_rows"] += 1
            try:
                payload = json.loads(row["review_work_json"])
            except ValueError:
                self.issue("invalid_review_work_json", ref)
                continue
            if not isinstance(payload, list):
                self.issue("invalid_review_work_list", ref)
                continue
            if not payload:
                self.issue("empty_legacy_review_owner", ref, level="warning")
            known: set[tuple[str, str]] = set()
            for index, value in enumerate(payload):
                item_ref = f"{ref}/{index}"
                self.counts["review_rows"] += 1
                try:
                    work = ReviewWork.model_validate(value)
                except ValueError:
                    self.issue("invalid_review_work", item_ref)
                    continue
                identity = (work.purpose, work.memo_key)
                if identity in known:
                    self.issue("duplicate_review_owner", item_ref)
                known.add(identity)
                if work.context is not None and (
                    work.context.number != row["number"] or work.context.purpose != work.purpose
                ):
                    self.issue("review_context_owner_mismatch", item_ref)
                if work.input_bill_json:
                    self.snapshot(work.input_bill_json, f"{item_ref}/input_bill_json")
                state = "abandoned" if work.abandoned else "context" if work.context else "snapshot"
                self.counts[f"review_{state}"] += 1
                self.counts[f"review_purpose_{work.purpose}"] += 1
                self.owners.append(
                    {
                        "ref": item_ref,
                        "kind": "review",
                        "term": row["term"],
                        "number": row["number"],
                        "purpose": work.purpose,
                        "state": state,
                        "since": work.since.isoformat(),
                        "checked_at": work.checked_at.isoformat() if work.checked_at else None,
                        "baseline_day": work.baseline_day.isoformat(),
                        "recorded_question_key": bool(work.memo_key),
                        "recorded_source_key": bool(work.source_key),
                        "comparison_snapshot": work.comparison_document is not None,
                        "protected_memo_rows": self.memos(connection, row["term"], row["number"]),
                    }
                )

    def snapshot(self, raw: str | None, ref: str) -> None:
        if raw is None:
            return
        if not raw:
            self.issue("legacy_snapshot_unavailable", ref, level="warning")
            return
        try:
            value = json.loads(raw)
        except ValueError:
            self.issue("invalid_work_snapshot", ref)
            return
        if not isinstance(value, dict):
            self.issue("invalid_work_snapshot", ref)

    @staticmethod
    def memos(connection: sqlite3.Connection, term: int, number: str) -> int:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM analysis_memo WHERE term=? AND number=?", (term, number)
            ).fetchone()[0]
        )


def audit(
    database: Path, *, observed_at: datetime | None = None, include_timing_witnesses: bool = False
) -> dict[str, Any]:
    if include_timing_witnesses and observed_at is None:
        raise ValueError("Timing witnesses require an explicit observed time")
    if observed_at is not None and observed_at.utcoffset() is None:
        raise ValueError("Observed time must include a timezone")
    result = WorkAudit()
    with open_snapshot(database) as snapshot:
        connection = snapshot.connection
        schema = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = sorted(_REQUIRED - tables)
        if not missing:
            result.documents(connection)
            result.reviews(connection)
        latencies = (
            (
                observed_timing(connection, observed_at)
                if include_timing_witnesses
                else observed_latency(connection, observed_at)
            )
            if observed_at is not None
            else None
        )
        llm_ages = llm_work_ages(connection, observed_at) if observed_at is not None else None
        if not snapshot.unchanged():
            result.issue("database_changed_during_audit", "database")
    result.findings.sort(key=lambda item: (item["level"], item["code"], item["ref"]))
    result.counts["errors"] = sum(item["level"] == "error" for item in result.findings)
    result.counts["warnings"] = sum(item["level"] == "warning" for item in result.findings)
    observed = result.counts["document_rows"] + result.counts["review_owner_rows"]
    status = (
        "failed"
        if result.counts["errors"]
        else "not_run"
        if missing or not observed
        else "recorded"
    )
    report: dict[str, Any] = {
        "version": 1,
        "status": status,
        "database_sha256": snapshot.sha256,
        "schema_version": schema,
        "missing_tables": missing,
        "summary": dict(sorted(result.counts.items())),
        "owners": result.owners,
        "findings": result.findings,
        "scope": "Saved document/review ownership and dependency metadata; no repair or execution.",
        "exclusions": [
            "No source refresh, payload reconstruction, model call, submission or delivery.",
            "No recomputation of recorded keys, legal applicability or current ELI verdict.",
            "Owner memo counts overlap; they are not distinct rows safe to delete.",
            "Publication linkage does not establish the configured channel or edit eligibility.",
        ],
    }
    if observed_at is not None:
        ages = work_ages(
            result.owners, observed_at, complete=not missing and not result.counts["errors"]
        )
        if result.document_defer_reasons:
            ages["document_wait_ages_by_last_defer_reason"] = result.document_wait_ages_by_reason(
                observed_at
            )
        report["work_ages"] = ages
        assert latencies is not None, "The explicit observation clock enables latency inspection"
        report["latencies"] = latencies.model_dump(mode="json")
        report["llm_work_ages"] = llm_ages
    return report


def run(
    database: Path,
    output: Path,
    *,
    observed_at: datetime | None = None,
    include_timing_witnesses: bool = False,
) -> int:
    if output.resolve() == database.resolve():
        raise ValueError("Report must be separate from the input database")
    report = audit(
        database, observed_at=observed_at, include_timing_witnesses=include_timing_witnesses
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    return 1 if report["status"] == "failed" else 2 if report["status"] == "not_run" else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--observed-at", type=datetime.fromisoformat)
    parser.add_argument("--timing-witnesses", action="store_true")
    args = parser.parse_args()
    try:
        code = run(
            args.database,
            args.output,
            observed_at=args.observed_at,
            include_timing_witnesses=args.timing_witnesses,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))
    raise SystemExit(code)


if __name__ == "__main__":
    main()

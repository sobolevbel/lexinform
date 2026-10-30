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
from lexinform.models import AmendmentRevisionApplicationWork, ReviewWork
from tools.corpus_artifacts import write_json
from tools.observed_latency import observed_latency
from tools.sqlite_snapshot import open_snapshot

_REQUIRED = {"bills", "document_work", "status_changes", "publications", "analysis_memo"}


def work_ages(
    owners: list[dict[str, Any]], observed_at: datetime, *, complete: bool
) -> dict[str, Any]:
    """Age saved nonterminal owners from their recorded work timestamp, never a source date."""
    if observed_at.utcoffset() is None:
        raise ValueError("Observed time must include a timezone")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    excluded: Counter[str] = Counter()
    for owner in owners:
        kind, state = owner["kind"], owner["state"]
        nonterminal = (kind, state) in (
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
        field_name = "since" if kind == "review" else "created_at"
        ages: list[tuple[int, str]] = []
        unknown: Counter[str] = Counter()
        for owner in items:
            raw = owner.get(field_name)
            if not isinstance(raw, str) or not raw:
                unknown["missing_timestamp"] += 1
                continue
            try:
                started = datetime.fromisoformat(raw)
            except ValueError:
                unknown["invalid_timestamp"] += 1
                continue
            if started.utcoffset() is None:
                unknown["timezone_unknown"] += 1
                continue
            elapsed = (observed_at - started).total_seconds()
            if elapsed < 0:
                unknown["timestamp_after_observation"] += 1
                continue
            ages.append((int(elapsed), owner["ref"]))
        oldest = max(ages, key=lambda item: item[0]) if ages else None
        records.append(
            {
                "kind": kind,
                "state": state,
                "count": len(items),
                "timestamp_field": field_name,
                "known_ages": len(ages),
                "unknown_ages": dict(sorted(unknown.items())),
                "oldest_seconds": oldest[0] if oldest is not None else None,
                "oldest_ref": oldest[1] if oldest is not None else None,
            }
        )
    return {
        "observed_at": observed_at.astimezone(UTC).isoformat(),
        "coverage": "recorded" if complete else "partial",
        "nonterminal_owners": sum(row["count"] for row in records),
        "groups": records,
        "excluded_terminal_owners": dict(sorted(excluded.items())),
        "scope": (
            "Saved work age in whole seconds; runnable state, enabled flags "
            "and source/delivery latency are not evaluated."
        ),
    }


@dataclass
class WorkAudit:
    counts: Counter[str] = field(default_factory=Counter)
    findings: list[dict[str, str]] = field(default_factory=list)
    owners: list[dict[str, Any]] = field(default_factory=list)

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


def audit(database: Path, *, observed_at: datetime | None = None) -> dict[str, Any]:
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
        latencies = observed_latency(connection, observed_at) if observed_at is not None else None
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
        report["work_ages"] = work_ages(
            result.owners, observed_at, complete=not missing and not result.counts["errors"]
        )
        assert latencies is not None, "The explicit observation clock enables latency inspection"
        report["latencies"] = latencies.model_dump(mode="json")
    return report


def run(database: Path, output: Path, *, observed_at: datetime | None = None) -> int:
    if output.resolve() == database.resolve():
        raise ValueError("Report must be separate from the input database")
    report = audit(database, observed_at=observed_at)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    return 1 if report["status"] == "failed" else 2 if report["status"] == "not_run" else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--observed-at", type=datetime.fromisoformat)
    args = parser.parse_args()
    try:
        code = run(args.database, args.output, observed_at=args.observed_at)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))
    raise SystemExit(code)


if __name__ == "__main__":
    main()

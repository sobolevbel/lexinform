"""Inspect editorial versions and database delivery confirmations without replaying any work."""

import argparse
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lexinform.adapters.content_source_refs import verify_content_source
from lexinform.content_versions import confirmed_content_version, content_delivery_mismatch
from lexinform.models import (
    ContentSnapshot,
    ContentVersion,
    ContentVersionSummary,
    DeliveryPlan,
    DeliveryResolution,
)
from tools.corpus_artifacts import write_json
from tools.sqlite_snapshot import open_snapshot

_TABLES = {"content_snapshots", "content_versions", "publication_content"}


def _frozen_content_issue(
    raw: str, version: ContentVersion, snapshots: dict[str, ContentSnapshot]
) -> str | None:
    try:
        plan = DeliveryPlan.model_validate_json(raw)
        mismatch = content_delivery_mismatch(version, snapshots, plan)
    except ValueError, TypeError:
        return "invalid_content_frozen_context"
    return "content_frozen_" + mismatch + "_mismatch" if mismatch else None


@dataclass
class _Findings:
    rows: list[dict[str, str]] = field(default_factory=list)

    def add(self, code: str, ref: str, *, warning: bool = False) -> None:
        self.rows.append({"code": code, "ref": ref, "level": "warning" if warning else "error"})


def _version_metadata(version: ContentVersion, findings: _Findings) -> dict[str, Any]:
    summary = ContentVersionSummary.from_version(version)
    for count in (
        summary.source_observation_ids_opaque_count,
        summary.computation_ids_opaque_count,
    ):
        if count:
            findings.add("content_reference_format_unknown", version.content_id, warning=True)
    for _ in summary.unrecognized_labels:
        findings.add("content_label_format_unknown", version.content_id, warning=True)
    return summary.model_dump(mode="json")


def _read_history(
    db: sqlite3.Connection,
    tables: set[str],
    counts: Counter[str],
    findings: _Findings,
) -> tuple[dict[str, ContentSnapshot], dict[str, ContentVersion]]:
    snapshots: dict[str, ContentSnapshot] = {}
    versions: dict[str, ContentVersion] = {}
    if "content_snapshots" in tables:
        counts["snapshot_rows"] = 0
        for row in db.execute("SELECT rowid AS audit_id, * FROM content_snapshots ORDER BY rowid"):
            counts["snapshot_rows"] += 1
            ref = f"content_snapshots/{row['audit_id']}"
            try:
                value = ContentSnapshot(kind=row["kind"], payload_json=row["payload_json"])
            except ValueError, TypeError:
                findings.add("invalid_content_snapshot", ref)
                continue
            if value.snapshot_id != row["snapshot_id"]:
                findings.add("content_snapshot_identity_mismatch", ref)
                continue
            snapshots[value.snapshot_id] = value
    if "content_versions" in tables:
        counts["version_rows"] = 0
        for row in db.execute("SELECT rowid AS audit_id, * FROM content_versions ORDER BY rowid"):
            counts["version_rows"] += 1
            ref = f"content_versions/{row['audit_id']}"
            try:
                version = ContentVersion.model_validate_json(row["version_json"])
            except ValueError, TypeError:
                findings.add("invalid_content_version", ref)
                continue
            expected = (
                version.content_id,
                version.root_publication_id,
                version.previous_version_id,
                version.bill_snapshot_id,
                version.change_snapshot_id,
                version.plan_snapshot_id,
            )
            columns = (
                "content_id",
                "root_publication_id",
                "previous_version_id",
                "bill_snapshot_id",
                "change_snapshot_id",
                "plan_snapshot_id",
            )
            if expected != tuple(row[key] for key in columns):
                findings.add("content_version_columns_mismatch", ref)
                continue
            versions[version.content_id] = version
    for version in versions.values():
        try:
            verify_content_source(db, version)
        except ValueError:
            findings.add("content_source_reference_invalid", version.content_id)
        for kind, identity in (
            ("bill", version.bill_snapshot_id),
            ("change", version.change_snapshot_id),
            ("plan", version.plan_snapshot_id),
        ):
            saved = snapshots.get(identity)
            if saved is None or saved.kind != kind:
                findings.add("content_snapshot_unavailable", version.content_id)
        if version.previous_version_id is not None:
            previous = versions.get(version.previous_version_id)
            if previous is None or previous.root_publication_id != version.root_publication_id:
                findings.add("content_predecessor_unavailable", version.content_id)
    return snapshots, versions


def _publication_bindings(
    db: sqlite3.Connection,
    tables: set[str],
    counts: Counter[str],
    findings: _Findings,
    snapshots: dict[str, ContentSnapshot],
    versions: dict[str, ContentVersion],
) -> dict[str, list[dict[str, Any]]]:
    bindings: dict[str, list[dict[str, Any]]] = {}
    if "publication_content" in tables:
        counts["binding_rows"] = 0
        for row in db.execute("SELECT * FROM publication_content ORDER BY publication_id"):
            counts["binding_rows"] += 1
            identity = row["publication_id"]
            bound = versions.get(row["content_id"])
            if bound is None:
                findings.add("content_version_unavailable", f"publication_content/{identity}")
                continue
            publication = (
                db.execute("SELECT * FROM publications WHERE id=?", (identity,)).fetchone()
                if "publications" in tables
                else None
            )
            physical: dict[str, Any] = {
                "publication_id": identity,
                "available": publication is not None,
                "operator_decisions": [],
            }
            if publication is None:
                findings.add("content_publication_unavailable", str(identity), warning=True)
            else:
                if publication["delivery_json"] is not None:
                    code = _frozen_content_issue(publication["delivery_json"], bound, snapshots)
                    if code is not None:
                        findings.add(code, str(identity))
                physical.update(
                    {key: publication[key] for key in ("status", "message_id", "attempts", "kind")}
                )
                root = db.execute(
                    "SELECT * FROM publications WHERE id=?", (bound.root_publication_id,)
                ).fetchone()
                if root is not None and tuple(
                    publication[key] for key in ("term", "number", "channel_id")
                ) != tuple(root[key] for key in ("term", "number", "channel_id")):
                    findings.add("content_owner_mismatch", str(identity))
                if (identity == bound.root_publication_id) != (bound.previous_version_id is None):
                    findings.add("content_binding_role_mismatch", str(identity))
                if (
                    identity != bound.root_publication_id
                    and publication["kind"] != "amendments_revision"
                ):
                    findings.add("content_binding_kind_mismatch", str(identity))
                if (
                    root is not None
                    and identity != bound.root_publication_id
                    and publication["status"] == "sent"
                    and publication["message_id"] != root["message_id"]
                ):
                    findings.add("content_message_target_mismatch", str(identity))
            if "delivery_resolutions" in tables:
                for resolution in db.execute(
                    "SELECT id, command_update_id, resolution_json FROM delivery_resolutions"
                    " WHERE publication_id=? ORDER BY id",
                    (identity,),
                ):
                    try:
                        decision = DeliveryResolution.model_validate_json(
                            resolution["resolution_json"]
                        )
                    except ValueError, TypeError:
                        findings.add("invalid_content_operator_decision", str(resolution["id"]))
                        continue
                    if (
                        decision.publication_id != identity
                        or decision.command.update_id != resolution["command_update_id"]
                    ):
                        findings.add("content_operator_columns_mismatch", str(resolution["id"]))
                        continue
                    physical["operator_decisions"].append(
                        {
                            "action": decision.action,
                            "resolved_at": decision.resolved_at.isoformat(),
                            "message_id": decision.message_id,
                        }
                    )
            bindings.setdefault(bound.content_id, []).append(physical)
    return bindings


def _history_rows(
    versions: dict[str, ContentVersion],
    bindings: dict[str, list[dict[str, Any]]],
    findings: _Findings,
    publication_id: int | None,
) -> list[dict[str, Any]]:
    roots = sorted({value.root_publication_id for value in versions.values()})
    if publication_id is not None:
        roots = [
            root
            for root in roots
            if root == publication_id
            or any(
                publication_id == physical["publication_id"]
                for version in versions.values()
                if version.root_publication_id == root
                for physical in bindings.get(version.content_id, [])
            )
        ]
    histories: list[dict[str, Any]] = []
    for root in roots:
        members = [version for version in versions.values() if version.root_publication_id == root]
        confirmed = {
            version.content_id
            for version in members
            if any(
                physical.get("status") == "sent" and physical.get("message_id") is not None
                for physical in bindings.get(version.content_id, [])
            )
        }
        confirmation = confirmed_content_version(
            {version.content_id: version.previous_version_id for version in members}, confirmed
        )
        if confirmation.ambiguous:
            findings.add("content_confirmed_branch", str(root))
        histories.append(
            {
                "root_publication_id": root,
                "original_content_ids": [
                    version.content_id for version in members if version.previous_version_id is None
                ],
                "current_content_id": confirmation.current_id,
                "confirmation_scope": "DB sent/message-ID evidence; no independent Telegram read.",
                "versions": [
                    {
                        **_version_metadata(version, findings),
                        "content_id": version.content_id,
                        "publications": bindings.get(version.content_id, []),
                    }
                    for version in members
                ],
            }
        )
    return histories


def audit(database: Path, *, publication_id: int | None = None) -> dict[str, Any]:
    if publication_id is not None and publication_id <= 0:
        raise ValueError("Publication identity must be positive")
    findings = _Findings()
    counts: Counter[str] = Counter()
    with open_snapshot(database) as snapshot:
        db = snapshot.connection
        schema = db.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = sorted(_TABLES - tables)
        if schema >= 55 or _TABLES.intersection(tables):
            for name in missing:
                findings.add("content_history_table_unavailable", name)
        snapshots, versions = _read_history(db, tables, counts, findings)
        bindings = _publication_bindings(db, tables, counts, findings, snapshots, versions)
        if not snapshot.unchanged():
            findings.add("database_changed_during_audit", "database")
    histories = _history_rows(versions, bindings, findings, publication_id)
    counts["errors"] = sum(row["level"] == "error" for row in findings.rows)
    counts["warnings"] = sum(row["level"] == "warning" for row in findings.rows)
    if counts["errors"]:
        for history in histories:
            history["current_content_id"] = None
    return {
        "version": 1,
        "status": "failed" if counts["errors"] else "recorded" if histories else "not_run",
        "database_sha256": snapshot.sha256,
        "schema_version": schema,
        "missing_tables": missing,
        "summary": dict(sorted(counts.items())),
        "histories": histories,
        "findings": findings.rows,
        "scope": "Editorial versions and attempts; no body/URL export, revalidation or execution.",
    }


def run(database: Path, output: Path, *, publication_id: int | None = None) -> int:
    if output.resolve() == database.resolve():
        raise ValueError("Report must be separate from the input database")
    report = audit(database, publication_id=publication_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    return 1 if report["status"] == "failed" else 2 if report["status"] == "not_run" else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--publication", type=int)
    args = parser.parse_args()
    try:
        code = run(args.database, args.output, publication_id=args.publication)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))
    raise SystemExit(code)


if __name__ == "__main__":
    main()

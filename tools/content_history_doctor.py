"""Inspect editorial versions and database delivery confirmations without replaying any work."""

import argparse
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lexinform.adapters.content_applications import verify_content_applications
from lexinform.adapters.content_history import (
    ContentVersionRowError,
    card_current_content,
    card_refresh_rows,
    read_content_publication,
    read_content_version,
    read_grouped_stage_telegram_target,
    verify_act_published_content,
    verify_card_content,
    verify_direct_stage_content,
    verify_grouped_stage_content,
    verify_multipart_card_revision,
    verify_original_delivery_content,
    verify_sitting_content,
)
from lexinform.adapters.content_source_refs import verify_content_source
from lexinform.adapters.digest_content import ResolvedDigestRenderedContent, verify_digest_content
from lexinform.content_versions import (
    confirmed_content_version,
    confirmed_multipart_card,
    content_delivery_mismatch,
    content_snapshot_refs,
    grouped_revision_obscures_current,
)
from lexinform.models import (
    ContentSnapshot,
    ContentVersion,
    ContentVersionSummary,
    DeliveryPlan,
    PublicationKind,
    PublicationStatus,
)
from lexinform.models.commands import read_delivery_resolution
from lexinform.models.original_delivery_content import ORIGINAL_CARD_KINDS, REVIEW_NOTICE_KINDS
from tools.content_review_evidence import review_application_evidence
from tools.corpus_artifacts import write_json
from tools.sqlite_snapshot import open_snapshot, snapshot_files

_TABLES = {"content_snapshots", "content_versions", "publication_content"}


def _multipart_card_profile(version: ContentVersion) -> bool:
    return version.multipart_card_revision is not None or (
        version.original_delivery_evidence is not None
        and version.original_delivery_evidence.kind is PublicationKind.NEW_BILL
    )


def _independent_profile(version: ContentVersion) -> bool:
    return (
        _multipart_card_profile(version)
        or version.sitting_profile is not None
        or version.act_published_evidence is not None
        or (
            version.direct_stage_evidence is not None
            and version.direct_stage_evidence.multipart is not None
        )
        or (
            version.original_delivery_evidence is not None
            and version.original_delivery_evidence.kind not in ORIGINAL_CARD_KINDS
        )
    )


def _frozen_content_issue(
    raw: str, version: ContentVersion, snapshots: dict[str, ContentSnapshot]
) -> str | None:
    try:
        plan = DeliveryPlan.model_validate_json(raw)
        mismatch = content_delivery_mismatch(version, snapshots, plan)
    except ValueError, TypeError, RecursionError, sqlite3.Error, IndexError, KeyError:
        return "invalid_content_frozen_context"
    return "content_frozen_" + mismatch + "_mismatch" if mismatch else None


@dataclass
class _Findings:
    rows: list[dict[str, str]] = field(default_factory=list)

    def add(self, code: str, ref: str, *, warning: bool = False) -> None:
        self.rows.append({"code": code, "ref": ref, "level": "warning" if warning else "error"})


def _version_metadata(
    version: ContentVersion, findings: _Findings, *, validated_primary_refs: bool
) -> dict[str, Any]:
    summary = ContentVersionSummary.from_version(version)
    for count in (
        summary.source_observation_ids_opaque_count,
        summary.computation_ids_opaque_count,
    ):
        if count:
            findings.add("content_reference_format_unknown", version.content_id, warning=True)
    for _ in summary.unrecognized_labels:
        findings.add("content_label_format_unknown", version.content_id, warning=True)
    metadata = summary.model_dump(mode="json")
    if validated_primary_refs and version.card_primary_evidence is not None:
        metadata["card_primary_evidence"] = version.card_primary_evidence.model_dump(mode="json")
    return metadata


def _read_history(
    db: sqlite3.Connection,
    tables: set[str],
    counts: Counter[str],
    findings: _Findings,
) -> tuple[dict[str, ContentSnapshot], dict[str, ContentVersion], set[str]]:
    snapshots: dict[str, ContentSnapshot] = {}
    versions: dict[str, ContentVersion] = {}
    validated_primary_refs: set[str] = set()
    if "content_snapshots" in tables:
        counts["snapshot_rows"] = 0
        for row in db.execute("SELECT rowid AS audit_id, * FROM content_snapshots ORDER BY rowid"):
            counts["snapshot_rows"] += 1
            ref = f"content_snapshots/{row['audit_id']}"
            try:
                value = ContentSnapshot(kind=row["kind"], payload_json=row["payload_json"])
            except ValueError, TypeError, RecursionError, sqlite3.Error, IndexError, KeyError:
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
                version = read_content_version(row)
            except ContentVersionRowError as exc:
                findings.add(exc.code, ref)
                continue
            versions[version.content_id] = version
    for version in versions.values():
        try:
            verify_content_applications(db, version)
        except ValueError:
            findings.add("content_application_reference_invalid", version.content_id)
        try:
            verify_content_source(db, version)
        except ValueError:
            findings.add("content_source_reference_invalid", version.content_id)
        try:
            verify_direct_stage_content(db, version, require_bound=True)
        except ValueError:
            findings.add("invalid_direct_stage_content_history", version.content_id)
        try:
            grouped = version.grouped_stage_evidence
            if grouped is not None and grouped.revision is None:
                if read_grouped_stage_telegram_target(db, version) is not None:
                    counts["grouped_numeric_telegram_receipts"] += 1
            else:
                verify_grouped_stage_content(db, version, require_bound=True)
        except ValueError:
            findings.add("invalid_grouped_stage_content_history", version.content_id)
        try:
            digest = verify_digest_content(db, version, require_bound=True)
            if isinstance(digest, ResolvedDigestRenderedContent):
                counts["digest_exact_rendered_parts"] += len(digest.parts)
                if digest.digest is None:
                    counts["digest_upstream_semantics_not_retained"] += 1
                else:
                    counts["digest_upstream_semantics_retained"] += 1
        except ValueError:
            findings.add("invalid_digest_content_history", version.content_id)
        if _independent_profile(version):
            for code, verify in (
                ("invalid_sitting_content_history", verify_sitting_content),
                ("invalid_act_content_history", verify_act_published_content),
                ("invalid_original_delivery_history", verify_original_delivery_content),
                ("invalid_multipart_card_history", verify_multipart_card_revision),
            ):
                try:
                    verify(db, version, require_bound=True)
                except ValueError:
                    findings.add(code, version.content_id)
        try:
            physical_id = verify_card_content(db, version, retained=True)
            if (
                version.change_snapshot_id is None
                and version.digest_profile is None
                and not _independent_profile(version)
                and physical_id is None
            ):
                findings.add("card_content_physical_unavailable", version.content_id, warning=True)
            if (
                version.card_primary_evidence is not None
                and physical_id == version.root_publication_id
            ):
                validated_primary_refs.add(version.content_id)
        except ValueError, TypeError, RecursionError, sqlite3.Error, IndexError, KeyError:
            findings.add("invalid_card_content_history", version.content_id)
        for kind, identity in content_snapshot_refs(version):
            saved = snapshots.get(identity)
            if saved is None or saved.kind != kind:
                findings.add("content_snapshot_unavailable", version.content_id)
        if version.previous_version_id is not None:
            previous = versions.get(version.previous_version_id)
            if previous is None or previous.root_publication_id != version.root_publication_id:
                findings.add("content_predecessor_unavailable", version.content_id)
    return snapshots, versions, validated_primary_refs


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
            if publication is not None:
                physical.update(
                    {key: publication[key] for key in ("status", "message_id", "attempts", "kind")}
                )
            if publication is None:
                findings.add("content_publication_unavailable", str(identity), warning=True)
            elif not _independent_profile(bound):
                if publication["delivery_json"] is not None and bound.digest_profile is None:
                    code = _frozen_content_issue(publication["delivery_json"], bound, snapshots)
                    if code is not None:
                        findings.add(code, str(identity))
                root = db.execute(
                    "SELECT * FROM publications WHERE id=?", (bound.root_publication_id,)
                ).fetchone()
                if root is not None and tuple(
                    publication[key] for key in ("term", "number", "channel_id")
                ) != tuple(root[key] for key in ("term", "number", "channel_id")):
                    findings.add("content_owner_mismatch", str(identity))
                if bound.digest_profile is None and (identity == bound.root_publication_id) != (
                    bound.previous_version_id is None
                ):
                    findings.add("content_binding_role_mismatch", str(identity))
                if (
                    bound.digest_profile is None
                    and identity != bound.root_publication_id
                    and publication["kind"]
                    != (
                        "message_part"
                        if bound.change_snapshot_id is None
                        or bound.reason == "grouped_stage_revision"
                        else "amendments_revision"
                    )
                ):
                    findings.add("content_binding_kind_mismatch", str(identity))
                if (
                    bound.digest_profile is None
                    and root is not None
                    and identity != bound.root_publication_id
                    and publication["status"] == "sent"
                    and publication["message_id"] != root["message_id"]
                ):
                    findings.add("content_message_target_mismatch", str(identity))
            if (
                bound.change_snapshot_id is None
                and bound.digest_profile is None
                and not _independent_profile(bound)
            ):
                try:
                    physical_id = verify_card_content(db, bound, retained=True)
                    if physical_id is not None and physical_id != identity:
                        findings.add("content_binding_kind_mismatch", str(identity))
                except ValueError, TypeError, RecursionError, sqlite3.Error, IndexError, KeyError:
                    findings.add("invalid_card_content_history", str(identity))
            if "delivery_resolutions" in tables:
                for resolution in db.execute(
                    "SELECT id, command_update_id, resolution_json FROM delivery_resolutions"
                    " WHERE publication_id=? ORDER BY id",
                    (identity,),
                ):
                    try:
                        decision = read_delivery_resolution(resolution["resolution_json"])
                    except (
                        ValueError,
                        TypeError,
                        RecursionError,
                        sqlite3.Error,
                        IndexError,
                        KeyError,
                    ):
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
    cards: dict[int, str | None],
    validated_primary_refs: set[str],
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
            if bindings.get(version.content_id)
            and (
                all(
                    physical.get("status") == "sent" and physical.get("message_id") is not None
                    for physical in bindings[version.content_id]
                )
                if version.digest_profile is not None or _independent_profile(version)
                else any(
                    physical.get("status") == "sent" and physical.get("message_id") is not None
                    for physical in bindings[version.content_id]
                )
            )
        }
        confirmation = confirmed_content_version(
            {version.content_id: version.previous_version_id for version in members}, confirmed
        )
        grouped_uncertain = any(
            grouped_revision_obscures_current(
                PublicationStatus(physical["status"]),
                retried=any(
                    decision["action"] == "retry" for decision in physical["operator_decisions"]
                ),
            )
            for version in members
            if version.reason == "grouped_stage_revision"
            for physical in bindings.get(version.content_id, [])
            if physical.get("status") in set(PublicationStatus)
        )
        if confirmation.ambiguous:
            findings.add("content_confirmed_branch", str(root))
        histories.append(
            {
                "root_publication_id": root,
                "original_content_ids": [
                    version.content_id for version in members if version.previous_version_id is None
                ],
                "current_content_id": None
                if grouped_uncertain
                else cards.get(root, confirmation.current_id),
                "confirmation_scope": "DB sent/message-ID evidence; no independent Telegram read.",
                "versions": [
                    {
                        **_version_metadata(
                            version,
                            findings,
                            validated_primary_refs=version.content_id in validated_primary_refs,
                        ),
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
        snapshots, versions, validated_primary_refs = _read_history(db, tables, counts, findings)
        try:
            bindings = _publication_bindings(db, tables, counts, findings, snapshots, versions)
        except sqlite3.Error, IndexError, KeyError:
            findings.add("content_publication_layout_unavailable", "publications")
            bindings = {}
        cards: dict[int, str | None] = {}
        multipart_roots = {
            value.root_publication_id
            for value in versions.values()
            if _multipart_card_profile(value)
        }
        bound_versions = {
            physical["publication_id"]: versions[identity]
            for identity, rows in bindings.items()
            for physical in rows
        }
        for root_id in multipart_roots:
            cards[root_id] = None
            try:
                row = db.execute("SELECT * FROM publications WHERE id=?", (root_id,)).fetchone()
                if row is None:
                    continue
                root = read_content_publication(row)
                aliases = db.execute(
                    "SELECT count(*) FROM publications WHERE kind='new_bill' AND status='sent'"
                    " AND channel_id=? AND message_id=?",
                    (root.channel_id, root.message_id),
                ).fetchone()[0]
                current = confirmed_multipart_card(
                    root,
                    [read_content_publication(part) for part in card_refresh_rows(db, root_id)],
                    alias_count=aliases,
                    version_for=bound_versions.get,
                    snapshot_for=snapshots.get,
                )
                if current is not None:
                    cards[root_id] = current.content_id
            except ValueError, TypeError, RecursionError, sqlite3.Error, IndexError, KeyError:
                findings.add("invalid_multipart_card_current_history", str(root_id))
        for value in versions.values():
            if (
                value.root_publication_id not in multipart_roots
                and value.change_snapshot_id is None
                and value.digest_profile is None
                and not _independent_profile(value)
            ):
                try:
                    cards[value.root_publication_id] = card_current_content(
                        db, value.root_publication_id
                    )
                except ValueError, TypeError, RecursionError, sqlite3.Error, IndexError, KeyError:
                    cards[value.root_publication_id] = None
        validated_primary_refs = {
            identity
            for identity in validated_primary_refs
            if any(physical["available"] for physical in bindings.get(identity, ()))
        }
        invalid_originals = {
            row["ref"]
            for row in findings.rows
            if row["code"] == "invalid_original_delivery_history"
        }
        review_refs: dict[str, dict[str, object]] = {}
        for identity, version in versions.items():
            original = version.original_delivery_evidence
            physical = bindings.get(identity, ())
            if (
                original is None
                or original.kind not in REVIEW_NOTICE_KINDS
                or identity in invalid_originals
                or not physical
                or not all(row["available"] for row in physical)
            ):
                continue
            try:
                semantic = snapshots[version.bill_context_id]
                review_refs[identity] = review_application_evidence(db, version, semantic)
            except ValueError, TypeError, KeyError, sqlite3.Error:
                review_refs[identity] = {"status": "invalid", "reason": "review_evidence_invalid"}
        if not snapshot.unchanged():
            findings.add("database_changed_during_audit", "database")
            validated_primary_refs.clear()
            review_refs.clear()
    histories = _history_rows(
        versions, bindings, findings, publication_id, cards, validated_primary_refs
    )
    for history in histories:
        for entry in history["versions"]:
            if entry["content_id"] in review_refs:
                entry["review_application_evidence"] = review_refs[entry["content_id"]]
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
    if output.resolve() in snapshot_files(database):
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

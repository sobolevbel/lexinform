"""Check recorded source decisions and their immutable references without fetching documents."""

import argparse
import datetime as dt
import sqlite3
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lexinform.adapters.sqlite_repo import RclLetterUseRowError, read_rcl_letter_use
from lexinform.document_editions import candidate_edition
from lexinform.models import (
    ArtifactMetadata,
    DocumentEdition,
    DocumentSelectionRecord,
    SelectionArtifactReference,
    SelectionEditionReference,
    SourceObservation,
)
from tools.artifact_metadata import (
    ArtifactMetadataIssue,
    ArtifactReadIssue,
    recorded_artifact_reads,
    recorded_artifacts,
)
from tools.corpus_artifacts import write_json
from tools.sqlite_snapshot import open_snapshot

_TABLES = {"document_selections", "source_observations"}
_ARTIFACT_TABLES = {"artifacts", "selection_artifacts"}
_EDITION_TABLES = {"document_editions", "selection_editions"}


def _audit_rcl_letters(
    connection: sqlite3.Connection,
    counts: Counter[str],
    issue: Callable[[str, str], None],
) -> None:
    for row in connection.execute(
        "SELECT rowid AS audit_id, * FROM rcl_letter_uses ORDER BY rowid"
    ):
        counts["rcl_letter_use_rows"] += 1
        ref = f"rcl_letter_uses/{row['audit_id']}"
        try:
            record = read_rcl_letter_use(connection, row)
        except RclLetterUseRowError as exc:
            issue(exc.code, ref)
            continue
        counts["rcl_letter_use_" + record.payload_state] += 1
        counts["rcl_letter_use_unknown_origin"] += record.payload.physical.artifact_sha256 is None
        counts["rcl_letter_use_unknown_stage_id"] += record.payload.official.stage_id is None


def audit(database: Path) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    findings: list[dict[str, str]] = []
    selections: dict[str, DocumentSelectionRecord] = {}
    referenced: set[str] = set()
    artifacts: dict[str, ArtifactMetadata] = {}
    linked_artifacts: set[str] = set()

    def issue(code: str, ref: str) -> None:
        findings.append({"code": code, "ref": ref, "level": "error"})

    with open_snapshot(database) as snapshot:
        connection = snapshot.connection
        schema = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = sorted(_TABLES - tables)
        if schema >= 52 or _TABLES.intersection(tables):
            for name in missing:
                issue("source_history_table_unavailable", name)
        missing_artifacts = sorted(_ARTIFACT_TABLES - tables)
        if schema >= 53 or _ARTIFACT_TABLES.intersection(tables):
            for name in missing_artifacts:
                issue("artifact_metadata_table_unavailable", name)
        if schema >= 54 and "artifact_reads" not in tables:
            issue("artifact_read_table_unavailable", "artifact_reads")
        if schema >= 57 or _EDITION_TABLES.intersection(tables):
            for name in sorted(_EDITION_TABLES - tables):
                issue("edition_metadata_table_unavailable", name)
        if schema >= 60 and "rcl_letter_uses" not in tables:
            issue("rcl_letter_use_table_unavailable", "rcl_letter_uses")
        if "rcl_letter_uses" in tables:
            _audit_rcl_letters(connection, counts, issue)
        if "document_selections" in tables:
            for row in connection.execute(
                "SELECT rowid AS audit_id, * FROM document_selections ORDER BY rowid"
            ):
                counts["selection_rows"] += 1
                ref = f"document_selections/{row['audit_id']}"
                try:
                    record = DocumentSelectionRecord.model_validate_json(row["record_json"])
                except ValueError, TypeError:
                    issue("invalid_document_selection", ref)
                    continue
                expected_selection = (
                    record.selection_id,
                    record.selection.question,
                    record.selection.policy_version,
                    record.selection.state,
                )
                if expected_selection != tuple(
                    row[key] for key in ("selection_id", "question", "policy_version", "state")
                ):
                    issue("document_selection_columns_mismatch", ref)
                selections[row["selection_id"]] = record
                counts[
                    "policy_unknown"
                    if record.selection.policy_version is None
                    else "policy_recorded"
                ] += 1
                counts["state/" + record.selection.state] += 1
        if "source_observations" in tables:
            for row in connection.execute(
                "SELECT rowid AS audit_id, * FROM source_observations ORDER BY rowid"
            ):
                counts["observation_rows"] += 1
                ref = f"source_observations/{row['audit_id']}"
                try:
                    observation = SourceObservation.model_validate_json(row["observation_json"])
                except ValueError, TypeError:
                    issue("invalid_source_observation", ref)
                    continue
                expected_observation = (
                    observation.observation_id,
                    observation.term,
                    observation.number,
                    observation.purpose,
                    observation.selection_id,
                    observation.document.url,
                    observation.document.kind,
                )
                columns = (
                    "observation_id",
                    "term",
                    "number",
                    "purpose",
                    "selection_id",
                    "source_url",
                    "source_kind",
                )
                if expected_observation != tuple(row[key] for key in columns):
                    issue("source_observation_columns_mismatch", ref)
                referenced.add(observation.selection_id)
                counts["purpose/" + observation.purpose] += 1
                if observation.selection_id not in selections:
                    issue("source_selection_unavailable", ref)
        if "artifacts" in tables:
            for artifact in recorded_artifacts(connection):
                counts["artifact_rows"] += 1
                if isinstance(artifact, ArtifactMetadataIssue):
                    issue("invalid_artifact_metadata", artifact.ref)
                    continue
                artifacts[artifact.sha256] = artifact
        if "selection_artifacts" in tables:
            for row in connection.execute(
                "SELECT rowid AS audit_id, * FROM selection_artifacts ORDER BY rowid"
            ):
                counts["artifact_link_rows"] += 1
                ref = f"selection_artifacts/{row['audit_id']}"
                try:
                    link = SelectionArtifactReference.model_validate(
                        {
                            name: row[name]
                            for name in (
                                "selection_id",
                                "part_scope",
                                "part_index",
                                "artifact_sha256",
                            )
                        }
                    )
                except ValueError, TypeError:
                    issue("invalid_artifact_link", ref)
                    continue
                linked_artifacts.add(link.artifact_sha256)
                if link.artifact_sha256 not in artifacts:
                    issue("artifact_metadata_unavailable", ref)
                selected = selections.get(link.selection_id)
                if selected is None:
                    issue("artifact_selection_unavailable", ref)
                    continue
                parts = (
                    selected.selection.parts
                    if link.part_scope == "parts"
                    else selected.selection.observed_parts
                )
                if link.part_index >= len(parts):
                    issue("artifact_part_out_of_scope", ref)
                elif parts[link.part_index].source_sha256 != link.artifact_sha256:
                    issue("artifact_outer_source_mismatch", ref)
        if "artifact_reads" in tables:
            for receipt in recorded_artifact_reads(connection):
                counts["artifact_read_rows"] += 1
                if isinstance(receipt, ArtifactReadIssue):
                    issue(receipt.code, receipt.ref)
                    continue
                parent = artifacts.get(receipt.sha256)
                if parent is None:
                    issue("artifact_read_body_unavailable", receipt.read_id)
                elif parent.byte_size != receipt.byte_size:
                    issue("artifact_read_size_mismatch", receipt.read_id)
        editions: dict[str, DocumentEdition] = {}
        if "document_editions" in tables:
            for row in connection.execute(
                "SELECT rowid AS audit_id, * FROM document_editions ORDER BY rowid"
            ):
                counts["edition_rows"] += 1
                ref = f"document_editions/{row['audit_id']}"
                try:
                    edition = DocumentEdition.model_validate_json(row["edition_json"])
                except ValueError, TypeError:
                    issue("invalid_document_edition", ref)
                    continue
                if (
                    edition.edition_id != row["edition_id"]
                    or edition.recorded_at.astimezone(dt.UTC).isoformat() != row["recorded_at"]
                ):
                    issue("edition_columns_mismatch", ref)
                editions[row["edition_id"]] = edition
        if "selection_editions" in tables:
            for row in connection.execute(
                "SELECT rowid AS audit_id, * FROM selection_editions ORDER BY rowid"
            ):
                counts["edition_link_rows"] += 1
                ref = f"selection_editions/{row['audit_id']}"
                try:
                    reference = SelectionEditionReference.model_validate(
                        {key: row[key] for key in ("selection_id", "edition_id")}
                    )
                except ValueError, TypeError:
                    issue("invalid_selection_edition", ref)
                    continue
                selection = selections.get(reference.selection_id)
                linked_edition = editions.get(reference.edition_id)
                if selection is None or linked_edition is None:
                    issue("edition_parent_unavailable", ref)
                elif (
                    candidate_edition(selection, recorded_at=linked_edition.recorded_at)
                    != linked_edition
                ):
                    issue("edition_membership_mismatch", ref)
        if not snapshot.unchanged():
            issue("database_changed_during_audit", "database")
    counts["unreferenced_selection_rows"] = len(set(selections) - referenced)
    counts["unlinked_artifact_rows"] = len(set(artifacts) - linked_artifacts)
    counts["errors"] = len(findings)
    observed = (
        sum(
            counts[name]
            for name in (
                "selection_rows",
                "observation_rows",
                "artifact_rows",
                "artifact_link_rows",
                "artifact_read_rows",
            )
        )
        + counts.get("edition_rows", 0)
        + counts.get("edition_link_rows", 0)
        + counts.get("rcl_letter_use_rows", 0)
    )
    return {
        "version": 1,
        "status": "failed" if findings else "recorded" if observed else "not_run",
        "database_sha256": snapshot.sha256,
        "schema_version": schema,
        "missing_tables": missing,
        "missing_artifact_tables": missing_artifacts,
        "artifact_reads_available": "artifact_reads" in tables,
        "rcl_letter_uses_available": "rcl_letter_uses" in tables,
        "summary": dict(sorted(counts.items())),
        "findings": findings,
        "scope": "Recorded source/artifact identities, SQL columns and outer-part references.",
        "exclusions": [
            "Current owner/source contents do not revalidate historical decisions.",
            "Unreferenced metadata is an inventory observation, not permission to delete it.",
            "Policy versions remain opaque; no historical policy or paid identity is inferred.",
            "No raw availability, extraction/legal completeness or provider-input replay verdict.",
            "RCL letter-use facts describe parsing and in-memory merging, not saved applications.",
            "Omitted RCL payloads cannot revalidate their absent full semantic metadata.",
            "No source bodies, URLs or evidence quotes in this report; no work is executed.",
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

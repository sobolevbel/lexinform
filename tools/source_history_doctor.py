"""Check recorded source decisions and their immutable references without fetching documents."""

import argparse
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from lexinform.models import (
    ArtifactMetadata,
    DocumentSelectionRecord,
    SelectionArtifactReference,
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
        if not snapshot.unchanged():
            issue("database_changed_during_audit", "database")
    counts["unreferenced_selection_rows"] = len(set(selections) - referenced)
    counts["unlinked_artifact_rows"] = len(set(artifacts) - linked_artifacts)
    counts["errors"] = len(findings)
    observed = sum(
        counts[name]
        for name in (
            "selection_rows",
            "observation_rows",
            "artifact_rows",
            "artifact_link_rows",
            "artifact_read_rows",
        )
    )
    return {
        "version": 1,
        "status": "failed" if findings else "recorded" if observed else "not_run",
        "database_sha256": snapshot.sha256,
        "schema_version": schema,
        "missing_tables": missing,
        "missing_artifact_tables": missing_artifacts,
        "artifact_reads_available": "artifact_reads" in tables,
        "summary": dict(sorted(counts.items())),
        "findings": findings,
        "scope": "Recorded source/artifact identities, SQL columns and outer-part references.",
        "exclusions": [
            "Current owner/source contents do not revalidate historical decisions.",
            "Unreferenced metadata is an inventory observation, not permission to delete it.",
            "Policy versions remain opaque; no historical policy or paid identity is inferred.",
            "No raw availability, extraction/legal completeness or provider-input replay verdict.",
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

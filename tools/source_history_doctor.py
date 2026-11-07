"""Check recorded source decisions and their immutable references without fetching documents."""

import argparse
import datetime as dt
import re
import sqlite3
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

from lexinform.adapters.candidate_trace_history import (
    CANDIDATE_TRACE_SCHEMA_VERSION,
    CandidateTraceRowError,
    read_candidate_disposition_trace,
    validate_candidate_trace_links,
)
from lexinform.adapters.content_source_refs import ContentSourceRows
from lexinform.adapters.extraction_history import (
    EXTRACTION_SCHEMA_VERSION,
    ExtractionResultRowError,
    read_extraction_result,
)
from lexinform.adapters.physical_history import physical_inventory_issues
from lexinform.adapters.senate_history import SenateInspectionRowError, read_senate_inspection
from lexinform.adapters.source_catalog_attempts import TABLES as CATALOG_TABLES
from lexinform.adapters.source_catalog_attempts import catalog_attempt_issues
from lexinform.adapters.source_omissions import source_omission_issues
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
from lexinform.models.documents import DocumentLimitation, SelectionState
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


def _audit_extraction_results(
    connection: sqlite3.Connection,
    counts: Counter[str],
    issue: Callable[[str, str], None],
) -> None:
    for row in connection.execute(
        "SELECT rowid AS audit_id, * FROM extraction_results ORDER BY rowid"
    ):
        counts["extraction_rows"] += 1
        identity = row["extraction_id"]
        ref = (
            f"extraction_results/{identity}"
            if isinstance(identity, str) and re.fullmatch(r"extraction-v1:[0-9a-f]{64}", identity)
            else f"extraction_results/row-{row['audit_id']}"
        )
        try:
            read_extraction_result(row)
        except ExtractionResultRowError as exc:
            issue(exc.code, ref)


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
        if not record.retained_identity_bound:
            counts["rcl_letter_use_legacy_unbound_omissions"] += 1
        counts["rcl_letter_use_unknown_origin"] += record.payload.physical.artifact_sha256 is None
        counts["rcl_letter_use_unknown_stage_id"] += record.payload.official.stage_id is None


def _audit_senate_inspections(
    connection: sqlite3.Connection, counts: Counter[str], issue: Callable[[str, str], None]
) -> None:
    for row in connection.execute(
        "SELECT rowid AS audit_id, * FROM senate_inspections ORDER BY rowid"
    ):
        counts["senate_inspection_rows"] += 1
        try:
            record = read_senate_inspection(connection, row)
        except SenateInspectionRowError as exc:
            issue(exc.code, f"senate_inspections/{row['audit_id']}")
            continue
        counts["senate_inspection_" + record.coverage] += 1
    counts["senate_inspection_link_rows"] = connection.execute(
        "SELECT count(*) FROM senate_inspection_sources"
    ).fetchone()[0]
    for row in connection.execute(
        "SELECT link.rowid AS audit_id FROM senate_inspection_sources AS link"
        " LEFT JOIN senate_inspections AS parent USING(inspection_id)"
        " WHERE parent.inspection_id IS NULL"
    ):
        issue("senate_inspection_orphan_link", f"senate_inspection_sources/{row['audit_id']}")


_COVERAGE_UNKNOWN = (
    "unknown_invalid_observation",
    "unknown_missing_selection",
    "unknown_invalid_selection",
    "unknown_identity_mismatch",
)


def _selection_coverage_details(
    connection: sqlite3.Connection, reader: ContentSourceRows, selection_id: str
) -> tuple[str, tuple[DocumentLimitation, ...] | None]:
    row = connection.execute(
        "SELECT record_json FROM document_selections WHERE selection_id=?", (selection_id,)
    ).fetchone()
    if row is None:
        return "unknown_missing_selection", None
    try:
        DocumentSelectionRecord.model_validate_json(row[0])
    except ValueError, TypeError:
        return "unknown_invalid_selection", None
    try:
        selection = reader.get_document_selection(selection_id)
    except ValueError, TypeError:
        return "unknown_identity_mismatch", None
    if selection is None:
        return "unknown_missing_selection", None
    value = selection.selection
    limitations = value.limitations if "limitations" in value.model_fields_set else None
    return value.state, limitations


def _selection_coverage_unavailable(connection: sqlite3.Connection) -> list[str]:
    columns = {
        "source_observations": {
            "observation_id",
            "term",
            "number",
            "purpose",
            "selection_id",
            "source_url",
            "source_kind",
            "observation_json",
        },
        "document_selections": {
            "selection_id",
            "question",
            "policy_version",
            "state",
            "record_json",
        },
    }
    return sorted(
        name
        for name, required in columns.items()
        if not required <= {row[1] for row in connection.execute(f"PRAGMA table_info({name})")}
    )


def selection_state_coverage(
    connection: sqlite3.Connection, *, include_limitations: bool = False
) -> dict[str, Any]:
    """Each persisted observation is one unit, including rows whose state cannot be verified."""
    unavailable = _selection_coverage_unavailable(connection)

    report: dict[str, Any] = {
        "status": "unavailable" if unavailable else "available",
        "unit": "persisted_source_observation_row",
        "denominator": "all_source_observation_rows",
        "total": None,
        "counts": None,
        "fractions": None,
        "groups": None,
        "unattributed": None,
        "unavailable_schema": sorted(unavailable),
    }
    limitation_report: dict[str, Any] = {
        "status": report["status"],
        "unit": report["unit"],
        "denominator": report["denominator"],
        "total": None,
        "known_limitations_rows": None,
        "no_recorded_limitations_rows": None,
        "unknown_rows": None,
        "unattributed": None,
        "counts": None,
        "fractions": None,
        "multi_label": True,
        "unavailable_schema": report["unavailable_schema"],
        "scope": (
            "Saved coded limitations per observation; labels overlap, "
            "not current blockers or legal completeness."
        ),
    }
    if include_limitations:
        report["limitation_coverage"] = limitation_report
    if unavailable:
        return report
    counts = dict.fromkeys((*get_args(SelectionState), *_COVERAGE_UNKNOWN), 0)
    groups: dict[tuple[str, str], Counter[str]] = {}
    reader = ContentSourceRows(connection)
    cached: dict[str, tuple[str, tuple[DocumentLimitation, ...] | None]] = {}
    limitation_counts = dict.fromkeys(get_args(DocumentLimitation), 0)
    limitation_unknown: Counter[str] = Counter()
    known_limitations = no_limitations = 0
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    for row in cursor.execute("SELECT observation_id, observation_json FROM source_observations"):
        try:
            SourceObservation.model_validate_json(row["observation_json"])
        except ValueError, TypeError:
            counts["unknown_invalid_observation"] += 1
            limitation_unknown["unknown_invalid_observation"] += 1
            continue
        try:
            observation = reader.get_source_observation(row["observation_id"])
        except ValueError, TypeError:
            observation = None
        if observation is None:
            counts["unknown_identity_mismatch"] += 1
            limitation_unknown["unknown_identity_mismatch"] += 1
            continue
        selection_id = observation.selection_id
        if selection_id not in cached:
            cached[selection_id] = _selection_coverage_details(connection, reader, selection_id)
        state, limitations = cached[selection_id]
        counts[state] += 1
        if limitations is None:
            limitation_unknown[
                state if state in _COVERAGE_UNKNOWN else "unknown_missing_limitations"
            ] += 1
        else:
            known_limitations += 1
            no_limitations += not limitations
            for limitation in set(limitations):
                limitation_counts[limitation] += 1
        if state not in _COVERAGE_UNKNOWN:
            key = (observation.purpose, observation.document.kind)
            groups.setdefault(key, Counter())[state] += 1
    total = sum(counts.values())
    limitation_report.update(
        total=total,
        known_limitations_rows=known_limitations,
        no_recorded_limitations_rows=no_limitations,
        unknown_rows={
            key: limitation_unknown[key]
            for key in (*_COVERAGE_UNKNOWN, "unknown_missing_limitations")
        },
        unattributed=sum(limitation_unknown.values()),
        counts=limitation_counts,
        fractions={
            key: count / total if total else None for key, count in limitation_counts.items()
        },
    )
    report.update(
        total=total,
        counts=counts,
        fractions={state: count / total if total else None for state, count in counts.items()},
        unattributed=sum(counts[state] for state in _COVERAGE_UNKNOWN),
        groups=[
            {
                "purpose": purpose,
                "document_kind": kind,
                "total": sum(group.values()),
                "fraction_denominator": total,
                "counts": {state: group[state] for state in counts},
                "fractions": {state: group[state] / total if total else None for state in counts},
            }
            for (purpose, kind), group in sorted(groups.items())
        ],
    )
    return report


_CANDIDATE_COLUMNS = {
    "candidate_disposition_traces": {"trace_id", "coverage", "candidate_count", "trace_json"},
    "source_candidate_traces": {"observation_id", "trace_id"},
    "source_observations": {
        "observation_id",
        "term",
        "number",
        "purpose",
        "selection_id",
        "source_url",
        "source_kind",
        "observation_json",
    },
}


def candidate_trace_inventory(
    connection: sqlite3.Connection, *, include_gaps: bool = False
) -> dict[str, Any]:
    """Validate saved trace rows and link parents without reconstructing policy or ownership."""
    unavailable = sorted(
        table
        for table, required in _CANDIDATE_COLUMNS.items()
        if not required <= {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    )
    findings: list[dict[str, str]] = []
    report: dict[str, Any] = {
        "status": "unavailable"
        if len(unavailable) == len(_CANDIDATE_COLUMNS)
        else "partial"
        if unavailable
        else "available",
        "unavailable_schema": unavailable,
        "trace_rows": None,
        "valid_trace_rows": None,
        "link_rows": None,
        "checked_links": None,
        "unknown_links": None,
        "findings": findings,
        "scope": (
            "Saved trace identity and referential presence; "
            "no current owner, legal or raw-input verdict."
        ),
    }

    def issue(code: str, ref: str) -> None:
        findings.append({"code": code, "ref": ref, "level": "error"})

    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    schema = connection.execute("PRAGMA user_version").fetchone()[0]
    for table in unavailable:
        if table in tables or schema >= CANDIDATE_TRACE_SCHEMA_VERSION:
            issue("candidate_trace_schema_unavailable", table)
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    traces: dict[str, bool] = {}
    annex_reasons: Counter[str] = Counter()
    required_reasons: Counter[str] = Counter()
    amendment_reasons: Counter[str] = Counter()
    supporting_reasons: Counter[str] = Counter()
    supporting_omitted = 0
    admitted_roles: Counter[str] = Counter()
    admitted_traces = admitted_omitted = 0
    try:
        if "candidate_disposition_traces" not in unavailable:
            report["trace_rows"] = report["valid_trace_rows"] = 0
            for row in cursor.execute(
                "SELECT rowid AS audit_id, * FROM candidate_disposition_traces ORDER BY rowid"
            ):
                report["trace_rows"] += 1
                try:
                    trace = read_candidate_disposition_trace(row)
                    validate_candidate_trace_links(connection, trace)
                except CandidateTraceRowError as exc:
                    traces[row["trace_id"]] = False
                    issue(exc.code, f"candidate_disposition_traces/row-{row['audit_id']}")
                else:
                    traces[row["trace_id"]] = True
                    report["valid_trace_rows"] += 1
                    if trace.admitted_support is not None:
                        admitted_traces += 1
                        admitted_roles.update(item.role for item in trace.admitted_support.rows)
                        admitted_omitted += trace.admitted_support.omitted
                    if include_gaps:
                        if trace.normative_annex_gap is not None:
                            annex_reasons[trace.normative_annex_gap.reason] += 1
                        if trace.required_part_gap is not None:
                            required_reasons[trace.required_part_gap.reason] += 1
                        if trace.amendment_part_gap is not None:
                            amendment_reasons[trace.amendment_part_gap.reason] += 1
                        if trace.supporting_exclusions is not None:
                            supporting_reasons.update(
                                row.reason for row in trace.supporting_exclusions.rows
                            )
                            supporting_omitted += trace.supporting_exclusions.omitted
        if "source_candidate_traces" not in unavailable:
            report["link_rows"] = report["checked_links"] = report["unknown_links"] = 0
            reader = ContentSourceRows(connection)
            for row in cursor.execute(
                "SELECT rowid AS audit_id, * FROM source_candidate_traces ORDER BY rowid"
            ):
                report["link_rows"] += 1
                ref = f"source_candidate_traces/row-{row['audit_id']}"
                if {"candidate_disposition_traces", "source_observations"}.intersection(
                    unavailable
                ):
                    report["unknown_links"] += 1
                    continue
                if row["trace_id"] not in traces:
                    issue("candidate_trace_parent_missing", ref)
                    continue
                if not traces[row["trace_id"]]:
                    issue("candidate_trace_parent_invalid", ref)
                    continue
                try:
                    source = reader.get_source_observation(row["observation_id"])
                except ValueError, TypeError:
                    issue("candidate_trace_source_invalid", ref)
                    continue
                if source is None:
                    issue("candidate_trace_source_missing", ref)
                    continue
                report["checked_links"] += 1
    finally:
        cursor.close()
    if admitted_traces:
        report["admitted_support"] = {
            "trace_rows_with_evidence": admitted_traces,
            "retained_parts_by_role": dict(sorted(admitted_roles.items())),
            "omitted_parts": admitted_omitted,
        }
    if include_gaps:
        total = report["trace_rows"]
        valid = report["valid_trace_rows"]
        report["gap_coverage"] = {
            "status": "unavailable" if total is None else "available",
            "denominator": "retained_candidate_trace_rows",
            "denominator_rows": total,
            "valid_trace_rows": valid,
            "invalid_trace_rows": total - valid if total is not None else None,
            "normative_annex": _gap_counts(valid, annex_reasons),
            "required_part": _gap_counts(valid, required_reasons),
            **(
                {"amendment_part": _gap_counts(valid, amendment_reasons)}
                if amendment_reasons
                else {}
            ),
            **(
                {
                    "supporting_exclusions": {
                        "retained_rows": supporting_reasons.total(),
                        "omitted_rows": supporting_omitted,
                        "reasons": dict(sorted(supporting_reasons.items())),
                    }
                }
                if supporting_reasons
                else {}
            ),
            "scope": (
                "Typed fields on validated saved traces; shared traces count once. "
                "Unrecorded reasons remain unknown. No raw or current blocker inference."
            ),
        }
    return report


def _gap_counts(valid: int | None, reasons: Counter[str]) -> dict[str, Any] | None:
    if valid is None:
        return None
    observed = sum(reasons.values())
    return {
        "recorded_rows": observed,
        "unknown_valid_rows": valid - observed,
        "reasons": dict(sorted(reasons.items())),
    }


def audit(
    database: Path,
    *,
    include_selection_coverage: bool = False,
    include_candidate_traces: bool = False,
    include_candidate_gaps: bool = False,
    include_selection_limitations: bool = False,
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    findings: list[dict[str, str]] = []
    selections: dict[str, DocumentSelectionRecord] = {}
    referenced: set[str] = set()
    artifacts: dict[str, ArtifactMetadata] = {}
    linked_artifacts: set[str] = set()
    coverage_report: dict[str, Any] | None = None
    candidate_report: dict[str, Any] | None = None

    def issue(code: str, ref: str) -> None:
        findings.append({"code": code, "ref": ref, "level": "error"})

    with open_snapshot(database) as snapshot:
        connection = snapshot.connection
        schema = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        coverage_unavailable = (
            set(_selection_coverage_unavailable(connection))
            if include_selection_coverage
            or include_candidate_traces
            or include_selection_limitations
            else set()
        )
        for name in sorted(coverage_unavailable & tables):
            issue("source_history_columns_unavailable", name)
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
        if schema >= EXTRACTION_SCHEMA_VERSION and "extraction_results" not in tables:
            issue("extraction_result_table_unavailable", "extraction_results")
        if "extraction_results" in tables:
            _audit_extraction_results(connection, counts, issue)
        if schema >= 57 or _EDITION_TABLES.intersection(tables):
            for name in sorted(_EDITION_TABLES - tables):
                issue("edition_metadata_table_unavailable", name)
        if schema >= 60 and "rcl_letter_uses" not in tables:
            issue("rcl_letter_use_table_unavailable", "rcl_letter_uses")
        if "rcl_letter_uses" in tables:
            _audit_rcl_letters(connection, counts, issue)
        senate_tables = {"senate_inspections", "senate_inspection_sources"}
        if schema >= 61 or senate_tables.intersection(tables):
            for name in sorted(senate_tables - tables):
                issue("senate_inspection_table_unavailable", name)
        if senate_tables <= tables:
            _audit_senate_inspections(connection, counts, issue)
        physical_tables = {"physical_metadata", "physical_captures"}
        if schema >= 62 or physical_tables.intersection(tables):
            for name in sorted(physical_tables - tables):
                issue("physical_inventory_table_missing", name)
        if physical_tables <= tables:
            for name in sorted(physical_tables):
                counts[name + "_rows"] = connection.execute(
                    f"SELECT COUNT(*) FROM {name}"
                ).fetchone()[0]
            for code, ref in physical_inventory_issues(connection):
                issue(code, ref)
        if schema >= 65 or CATALOG_TABLES.intersection(tables):
            for name in sorted(CATALOG_TABLES - tables):
                issue("source_catalog_table_missing", name)
        if tables >= CATALOG_TABLES:
            for name in sorted(CATALOG_TABLES):
                counts[name + "_rows"] = connection.execute(
                    f"SELECT COUNT(*) FROM {name}"
                ).fetchone()[0]
            for code, ref in catalog_attempt_issues(connection):
                issue(code, ref)
        omission_tables = {"source_metadata_omissions", "source_omission_parents"}
        if schema >= 63 or omission_tables.intersection(tables):
            for name in sorted(omission_tables - tables):
                issue("source_omission_table_missing", name)
        if omission_tables <= tables:
            for code, ref in source_omission_issues(connection):
                issue(code, ref)
        if "document_selections" in tables and "document_selections" not in coverage_unavailable:
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
        if "source_observations" in tables and "source_observations" not in coverage_unavailable:
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
        if include_selection_coverage or include_selection_limitations:
            coverage_report = selection_state_coverage(
                connection, include_limitations=include_selection_limitations
            )
        if include_candidate_traces or include_candidate_gaps:
            candidate_report = candidate_trace_inventory(
                connection, include_gaps=include_candidate_gaps
            )
            findings.extend(candidate_report["findings"])
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
        + counts.get("senate_inspection_rows", 0)
        + counts.get("senate_inspection_link_rows", 0)
        + counts.get("physical_metadata_rows", 0)
        + counts.get("physical_captures_rows", 0)
        + counts.get("source_catalog_snapshots_rows", 0)
        + counts.get("source_catalog_attempts_rows", 0)
    )
    report = {
        "version": 1,
        "status": "failed" if findings else "recorded" if observed else "not_run",
        "database_sha256": snapshot.sha256,
        "schema_version": schema,
        "missing_tables": missing,
        "missing_artifact_tables": missing_artifacts,
        "artifact_reads_available": "artifact_reads" in tables,
        "rcl_letter_uses_available": "rcl_letter_uses" in tables,
        "senate_inspections_available": {"senate_inspections", "senate_inspection_sources"}
        <= tables,
        "summary": dict(sorted(counts.items())),
        "findings": findings,
        "scope": "Recorded source/artifact identities, SQL columns and outer-part references.",
        "exclusions": [
            "Current owner/source contents do not revalidate historical decisions.",
            "Unreferenced metadata is an inventory observation, not permission to delete it.",
            "Policy versions remain opaque; no historical policy or paid identity is inferred.",
            "No raw availability, extraction/legal completeness or provider-input replay verdict.",
            "RCL letter-use facts describe parsing and in-memory merging, not saved applications.",
            "Senate inspection facts do not certify admission, paid inputs or saved applications.",
            "Omitted Senate universe/context metadata cannot be completely revalidated.",
            "Omitted RCL payloads cannot revalidate their absent full semantic metadata.",
            "Legacy v1 RCL omissions have unbound refs; v2 binds retained metadata only.",
            "No source bodies, URLs or evidence quotes in this report; no work is executed.",
        ],
    }

    if include_selection_limitations and coverage_report is not None:
        report["selection_limitation_coverage"] = coverage_report.pop("limitation_coverage")
    if include_selection_coverage:
        report["selection_state_coverage"] = coverage_report
    if include_candidate_gaps and candidate_report is not None:
        report["candidate_gap_coverage"] = candidate_report.pop("gap_coverage")
    if include_candidate_traces:
        report["candidate_traces"] = candidate_report
    return report


def run(
    database: Path,
    output: Path,
    *,
    include_selection_coverage: bool = False,
    include_candidate_traces: bool = False,
    include_candidate_gaps: bool = False,
    include_selection_limitations: bool = False,
) -> int:
    if output.resolve() == database.resolve():
        raise ValueError("Report must be separate from the input database")
    report = audit(
        database,
        include_selection_coverage=include_selection_coverage,
        include_candidate_traces=include_candidate_traces,
        include_candidate_gaps=include_candidate_gaps,
        include_selection_limitations=include_selection_limitations,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    return 1 if report["status"] == "failed" else 2 if report["status"] == "not_run" else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selection-coverage", action="store_true")
    parser.add_argument("--candidate-traces", action="store_true")
    parser.add_argument("--candidate-gaps", action="store_true")
    parser.add_argument("--selection-limitations", action="store_true")
    args = parser.parse_args()
    try:
        code = run(
            args.database,
            args.output,
            include_selection_coverage=args.selection_coverage,
            include_candidate_traces=args.candidate_traces,
            include_candidate_gaps=args.candidate_gaps,
            include_selection_limitations=args.selection_limitations,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))
    raise SystemExit(code)


if __name__ == "__main__":
    main()

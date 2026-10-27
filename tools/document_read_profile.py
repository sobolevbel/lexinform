"""Summarize recorded downloader observations without inferring missing historical traffic."""

import argparse
import datetime as dt
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from lexinform.models import DocumentReadMetrics
from tools.artifact_metadata import ArtifactMetadataIssue, recorded_artifacts
from tools.corpus_artifacts import write_json
from tools.saved_records import SavedTable, saved_columns
from tools.sqlite_snapshot import open_snapshot

_REPORTS = {"runs": SavedTable(("id",), ("report_json",))}
_METRIC_FIELDS = {"schema_version", "cache_hits", "distinct_bodies", "distinct_body_bytes", "hosts"}
_HOST_FIELDS = {"attempts", "completed", "body_bytes", "over_limit", "failed"}


def _day(value: object) -> str:
    if isinstance(value, str):
        try:
            time = dt.datetime.fromisoformat(value)
            if time.utcoffset() is not None:
                return time.astimezone(dt.UTC).date().isoformat()
        except ValueError:
            pass
    return "unknown"


def _totals(metrics: DocumentReadMetrics) -> Counter[str]:
    return Counter(
        {
            "measured_runs": 1,
            "cache_hits": metrics.cache_hits,
            "completed_body_bytes": metrics.body_bytes,
            "completed_bodies": metrics.completed,
            "sum_per_run_distinct_body_bytes": metrics.distinct_body_bytes,
            "sum_per_run_distinct_bodies": metrics.distinct_bodies,
            "within_run_duplicate_body_bytes": metrics.duplicate_body_bytes,
            "download_attempts": sum(host.attempts for host in metrics.hosts.values()),
            "over_limit_attempts": sum(host.over_limit for host in metrics.hosts.values()),
            "failed_attempts": sum(host.failed for host in metrics.hosts.values()),
            "unfinished_attempts": sum(
                host.attempts - host.completed - host.failed - host.over_limit
                for host in metrics.hosts.values()
            ),
        }
    )


def audit(database: Path) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    days: dict[str, Counter[str]] = defaultdict(Counter)
    hosts: dict[str, Counter[str]] = defaultdict(Counter)
    findings: list[dict[str, str]] = []
    catalogue: Counter[str] = Counter()
    catalogue_days: dict[str, Counter[str]] = defaultdict(Counter)

    def issue(code: str, ref: str, *, level: str = "error") -> None:
        findings.append({"code": code, "ref": ref, "level": level})

    with open_snapshot(database) as snapshot:
        connection = snapshot.connection
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for column in saved_columns(connection, tables, catalogue=_REPORTS):
            counts["saved_reports"] += 1
            if not isinstance(column.raw, str | bytes | bytearray):
                issue("invalid_run_report", column.ref)
                continue
            try:
                report = json.loads(column.raw)
            except ValueError, TypeError, RecursionError:
                issue("invalid_run_report", column.ref)
                continue
            if not isinstance(report, dict):
                issue("invalid_run_report", column.ref)
                continue
            day = _day(report.get("started_at"))
            raw = report.get("document_reads")
            if raw is None:
                counts["unmeasured_reports"] += 1
                days[day]["unmeasured_reports"] += 1
                continue
            if (
                isinstance(raw, dict)
                and type(raw.get("schema_version")) is int
                and raw["schema_version"] != 1
            ):
                counts["unsupported_metric_versions"] += 1
                issue("unsupported_document_read_version", column.ref, level="warning")
                continue
            if (
                not isinstance(raw, dict)
                or not raw.keys() >= _METRIC_FIELDS
                or type(raw["schema_version"]) is not int
                or not isinstance(raw["hosts"], dict)
                or any(
                    not isinstance(host, str)
                    or not host
                    or any(char.isspace() or char in "/@?#\\" for char in host)
                    for host in raw["hosts"]
                )
                or any(
                    not isinstance(host, dict) or not host.keys() >= _HOST_FIELDS
                    for host in raw["hosts"].values()
                )
            ):
                issue("incomplete_document_read_metrics", column.ref)
                continue
            try:
                metrics = DocumentReadMetrics.model_validate(raw)
            except ValueError, TypeError:
                issue("invalid_document_read_metrics", column.ref)
                continue
            counts["measured_reports"] += 1
            if day == "unknown":
                issue("run_recording_day_unknown", column.ref, level="warning")
            observed = _totals(metrics)
            totals.update(observed)
            days[day].update(observed)
            for host, values in metrics.hosts.items():
                hosts[host].update(values.model_dump())
        if "runs" in tables:
            rows = connection.execute("SELECT count(*) FROM runs").fetchone()[0]
            counts["rows_without_saved_report"] = rows - counts["saved_reports"]
        if "artifacts" in tables:
            catalogue.update({"known_unique_bodies": 0, "known_unique_body_bytes": 0})
            for artifact in recorded_artifacts(connection):
                if isinstance(artifact, ArtifactMetadataIssue):
                    issue("invalid_artifact_metadata", artifact.ref)
                    continue
                catalogue["known_unique_bodies"] += 1
                catalogue["known_unique_body_bytes"] += artifact.byte_size
                day = artifact.recorded_at.astimezone(dt.UTC).date().isoformat()
                catalogue_days[day]["first_recorded_bodies"] += 1
                catalogue_days[day]["first_recorded_body_bytes"] += artifact.byte_size
        if not snapshot.unchanged():
            issue("database_changed_during_audit", "database")
    counts["errors"] = sum(row["level"] == "error" for row in findings)
    counts["warnings"] = sum(row["level"] == "warning" for row in findings)
    return {
        "version": 1,
        "status": "failed"
        if counts["errors"]
        else "recorded"
        if counts["measured_reports"]
        else "not_run",
        "database_sha256": snapshot.sha256,
        "summary": dict(sorted(counts.items())),
        "totals": dict(sorted(totals.items())),
        "by_run_started_day_utc": {
            day: dict(sorted(row.items())) for day, row in sorted(days.items())
        },
        "by_host": {host: dict(sorted(row.items())) for host, row in sorted(hosts.items())},
        "findings": findings,
        "artifact_catalogue": {
            "available": "artifacts" in tables,
            "summary": dict(sorted(catalogue.items())),
            "by_first_recorded_day_utc": {
                day: dict(sorted(values.items())) for day, values in sorted(catalogue_days.items())
            },
            "scope": "Known complete-body identities; retention and past demand remain separate.",
        },
        "scope": "Known shared TextLoader observations in saved pipeline run reports.",
        "exclusions": [
            "Unmeasured is not zero; standalone CLI/direct gateway reads are outside scope.",
            "Completed-body bytes exclude wire overhead and unknown partial/refused responses.",
            "Summed per-run distinct bytes are not distinct archive storage across runs.",
            "Run-start day is our recording day, not the source publication or source latency.",
            "No file bodies, URLs, individual SHA, retention/backend choice or work execution.",
            "Artifact first-recording day is not the first download or source publication day.",
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

"""Inspect frozen request checksums and result references in a checkpointed SQLite copy."""

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.corpus_artifacts import write_json
from tools.sqlite_snapshot import open_snapshot

_SHA = re.compile(r"[0-9a-f]{64}")
_REQUIRED = {"llm_batch_intents", "llm_batch_items", "llm_batches", "bills", "analysis_memo"}
_RESULT_COLUMNS = {
    "bills": (
        "analysis_json",
        "ready_analysis_json",
        "joint_json",
        "supplements_json",
        "consultation_review_json",
        "commission_review_json",
        "act_application_review_json",
    ),
    "status_changes": ("amendments_json", "supplements_json"),
    "publications": ("delivery_json",),
    "analysis_memo": ("record_json",),
}
_ANSWERS = {"analysis", "amendments", "digest", "comparison", "proposal", "review"}


@dataclass
class RequestAudit:
    counts: Counter[str] = field(default_factory=Counter)
    inputs: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    findings: list[dict[str, str]] = field(default_factory=list)
    intent_artifacts: dict[str, dict[str, str | None]] = field(default_factory=dict)

    def issue(self, code: str, ref: str, *, level: str = "error") -> None:
        self.findings.append({"code": code, "ref": ref, "level": level})

    def load(self, raw: object, ref: str) -> Any:
        if not isinstance(raw, str):
            self.issue("invalid_json", ref)
            return None
        try:
            return json.loads(raw)
        except ValueError, RecursionError:
            self.issue("invalid_json", ref)
            return None

    def identity(self, value: object, ref: str, kind: str) -> str | None:
        if value is None:
            self.counts[f"{kind}_unknown"] += 1
            return None
        if not isinstance(value, str) or _SHA.fullmatch(value) is None:
            self.issue("invalid_request_sha256", ref)
            return None
        self.counts[f"{kind}_known"] += 1
        group = self.inputs.setdefault(value, {"items": [], "results": [], "intents": []})
        group[kind].append(ref)
        return value

    def intent(self, custom_id: str, raw: str) -> None:
        ref = f"llm_batch_intents/{custom_id}"
        self.counts["intents"] += 1
        request = self.load(raw, ref)
        if not isinstance(request, dict):
            self.issue("invalid_request_object", ref)
            return
        recorded = self.identity(request.get("payload_sha256"), ref, "intents")
        payload = request.get("payload_json")
        if payload is not None and not isinstance(payload, str):
            self.issue("invalid_frozen_payload", ref)
        observed = (
            hashlib.sha256(payload.encode()).hexdigest() if isinstance(payload, str) else None
        )
        self.intent_artifacts[ref] = {"recorded_sha256": recorded, "observed_sha256": observed}
        if recorded is not None:
            if observed != recorded:
                self.issue("payload_checksum_mismatch", ref)
            else:
                self.counts["verified_intent_payloads"] += 1
        elif observed is not None:
            self.counts["legacy_retained_payloads"] += 1
        else:
            self.issue("intent_payload_unavailable", ref, level="warning")

    def result(self, value: Any, ref: str, depth: int = 0) -> None:
        if depth > 50:
            self.issue("record_nesting_limit", ref)
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                self.result(child, f"{ref}/{index}", depth + 1)
        elif isinstance(value, dict):
            if "request_sha256" in value or (
                {"model", "prompt_version", "created_at"} <= value.keys()
                and _ANSWERS.intersection(value)
            ):
                self.counts["results"] += 1
                self.identity(value.get("request_sha256"), ref, "results")
            for key, child in value.items():
                if key in {"bill_json", "change_json"} and isinstance(child, str) and child:
                    child = self.load(child, f"{ref}/{key}")
                if isinstance(child, (dict, list)):
                    self.result(child, f"{ref}/{key}", depth + 1)

    def finish(self) -> None:
        for key in (
            "intents",
            "items",
            "results",
            "intents_known",
            "intents_unknown",
            "items_known",
            "items_unknown",
            "results_known",
            "results_unknown",
            "verified_intent_payloads",
            "legacy_retained_payloads",
            "repeated_inputs",
        ):
            self.counts[key] += 0
        for group in self.inputs.values():
            if group["results"] and not group["items"]:
                for ref in group["results"]:
                    self.issue("result_without_batch_receipt", ref, level="warning")
            if len(group["items"]) > 1:
                self.counts["repeated_inputs"] += 1
            for refs in group.values():
                refs.sort()
        self.findings.sort(key=lambda row: (row["level"], row["code"], row["ref"]))
        self.counts["errors"] = sum(row["level"] == "error" for row in self.findings)
        self.counts["warnings"] = sum(row["level"] == "warning" for row in self.findings)
        retained = {
            row["recorded_sha256"]
            for row in self.intent_artifacts.values()
            if row["recorded_sha256"] is not None
            and row["recorded_sha256"] == row["observed_sha256"]
        }
        self.counts["inputs_with_retained_payload"] = len(retained)
        self.counts["fingerprints_without_retained_payload"] = len(self.inputs) - len(retained)


def _rows(connection: sqlite3.Connection, audit: RequestAudit, tables: set[str]) -> None:
    for row in connection.execute(
        "SELECT custom_id, request_json FROM llm_batch_intents ORDER BY custom_id"
    ):
        audit.intent(row["custom_id"], row["request_json"])
    for row in connection.execute(
        "SELECT i.batch_id, i.custom_id, i.meta_json, b.provider FROM llm_batch_items i "
        "LEFT JOIN llm_batches b ON b.batch_id=i.batch_id ORDER BY i.batch_id, i.custom_id"
    ):
        ref = f"llm_batch_items/{row['batch_id']}/{row['custom_id']}"
        audit.counts["items"] += 1
        if row["provider"] is None:
            audit.issue("missing_batch", ref)
        meta = audit.load(row["meta_json"], ref)
        if not isinstance(meta, dict):
            audit.issue("invalid_item_metadata", ref)
        else:
            audit.identity(meta.get("request_sha256"), ref, "items")
    for table, candidates in _RESULT_COLUMNS.items():
        if table not in tables:
            continue
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        selected = [column for column in candidates if column in columns]
        keys = (
            ["term", "number"]
            if table == "bills"
            else ["key"]
            if table == "analysis_memo"
            else ["id"]
        )
        if not selected or not set(keys) <= columns:
            continue
        for row in connection.execute(
            f"SELECT {', '.join(keys + selected)} FROM {table} ORDER BY {', '.join(keys)}"
        ):
            ref = table + "/" + "/".join(str(row[key]) for key in keys)
            for column in selected:
                if row[column] is not None:
                    audit.result(audit.load(row[column], f"{ref}/{column}"), f"{ref}/{column}")


def audit(database: Path) -> dict[str, Any]:
    result = RequestAudit()
    with open_snapshot(database) as snapshot:
        connection = snapshot.connection
        schema = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = sorted(_REQUIRED - tables)
        if not missing:
            _rows(connection, result, tables)
        if not snapshot.unchanged():
            result.issue("database_changed_during_audit", "database")
    result.finish()
    observed = sum(result.counts[key] for key in ("intents", "items", "results"))
    status = (
        "failed"
        if result.counts["errors"]
        else "not_run"
        if missing or not observed
        else "recorded"
    )
    return {
        "version": 1,
        "status": status,
        "database_sha256": snapshot.sha256,
        "schema_version": schema,
        "missing_tables": missing,
        "summary": dict(sorted(result.counts.items())),
        "findings": result.findings,
        "inputs": dict(sorted(result.inputs.items())),
        "intent_artifacts": result.intent_artifacts,
        "scope": (
            "Saved request fingerprints and result references in a checkpointed SQLite copy; "
            "no repair, submission or inference of legacy history."
        ),
        "exclusions": [
            "Unknown fingerprints and unretained completed payloads are not corruption.",
            "Repeated input bytes do not prove duplicate billing or application.",
            "No blob, legal-selection, generation-ownership or complete-history audit.",
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

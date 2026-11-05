"""Inventory saved primary claim slots without exporting text or assessing support."""

import argparse
import sqlite3
from collections import Counter
from pathlib import Path

from lexinform.adapters.computation_history import ComputationRowError, read_computation_result
from lexinform.models import AnalysisRecord
from tools.corpus_artifacts import digest, write_json
from tools.sqlite_snapshot import open_snapshot


def audit(database: Path) -> dict[str, object]:
    rows: Counter[str] = Counter(validated_primary=0, failed_primary=0, other_purposes=0, invalid=0)
    claims: Counter[str] = Counter(
        key_changes=0, affected_groups=0, effective_date=0, changes_since_previous=0
    )
    invalid: Counter[str] = Counter()
    answers: list[dict[str, object]] = []
    blank_slots = 0
    empty_answers = 0
    with open_snapshot(database) as snapshot:
        db = snapshot.connection
        schema = db.execute("PRAGMA user_version").fetchone()[0]
        available = (
            db.execute(
                "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='computation_results'"
            ).fetchone()
            is not None
        )
        if available:
            for row in db.execute("SELECT * FROM computation_results ORDER BY result_id"):
                try:
                    result = read_computation_result(row)
                except ComputationRowError as exc:
                    rows["invalid"] += 1
                    invalid[exc.code] += 1
                    continue
                if result.purpose not in ("analysis", "reanalysis"):
                    rows["other_purposes"] += 1
                    continue
                if result.validation == "error":
                    rows["failed_primary"] += 1
                    continue
                record = result.record
                assert isinstance(record, AnalysisRecord), (
                    "the computation reader enforces the primary record type"
                )
                rows["validated_primary"] += 1
                answer = record.analysis
                fields = (
                    ("key_changes", tuple(enumerate(answer.key_changes))),
                    ("affected_groups", tuple(enumerate(answer.affected_groups))),
                    (
                        "effective_date",
                        () if answer.effective_date is None else ((None, answer.effective_date),),
                    ),
                    ("changes_since_previous", tuple(enumerate(answer.changes_since_previous))),
                )
                slots: list[dict[str, object]] = []
                for field, values in fields:
                    for index, value in values:
                        if not value.strip():
                            blank_slots += 1
                            continue
                        claims[field] += 1
                        slots.append(
                            {
                                "field": field,
                                "index": index,
                                "claim_text_sha256": digest(value.encode()),
                            }
                        )
                empty_answers += not slots
                answers.append(
                    {"result_id": result.result_id, "purpose": result.purpose, "claims": slots}
                )
        if not snapshot.unchanged():
            raise ValueError("Input database changed during the audit")
    return {
        "version": 1,
        "status": "not_run" if not available else "failed" if invalid else "recorded",
        "database_sha256": snapshot.sha256,
        "schema_version": schema,
        "scope": "Canonical computation_results rows, one answer per validated primary result.",
        "exclusions": [
            "Bills, memos, content snapshots and pre-ledger answers are outside this census.",
            "Free-form adoption or commencement assertions are not inferred.",
        ],
        "citation_evidence": "legacy_missing",
        "semantic": "semantic_not_assessed",
        "result_rows": sum(rows.values()),
        "row_counts": dict(rows),
        "invalid_counts": dict(sorted(invalid.items())),
        "nonblank_claim_counts": dict(claims),
        "blank_claim_slots": blank_slots,
        "answers_without_nonblank_claims": empty_answers,
        "answers": answers,
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

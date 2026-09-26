"""Audit a trusted, pinned state dump in a temporary database without running the pipeline."""

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from lexinform.adapters.sqlite_repo import SCHEMA_VERSION, SqliteBillRepository
from lexinform.models import Bill


def source(bill: Bill) -> str:
    if bill.is_rcl:
        return "rcl"
    if bill.is_pre_print:
        return "rpw"
    return "wykaz" if bill.is_wykaz else "sejm"


def audit(dump: Path, commit: str, committed_at: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("a full pinned commit SHA is required")
    raw = dump.read_bytes()
    script = raw.decode("utf-8")
    version = re.search(r"^PRAGMA user_version = (\d+);$", script, re.MULTILINE)
    dump_version = int(version[1]) if version else 1
    if dump_version > SCHEMA_VERSION:
        raise ValueError("unsupported future schema")
    with TemporaryDirectory(prefix="lexinform-website-audit-") as directory:
        path = Path(directory) / "state.db"
        repository = SqliteBillRepository(path)
        try:
            repository.restore(script)
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
                bills = []
                for term, number in db.execute(
                    "SELECT term, number FROM bills ORDER BY term, number"
                ):
                    bill = repository.get(term, number)
                    assert bill is not None
                    bills.append(bill)
                result = summarize(db, bills)
        finally:
            repository.close()
    result["fixture_version"] = 2
    result["source"] = {
        "commit": commit,
        "committed_at": committed_at,
        "dump_bytes": len(raw),
        "dump_sha256": hashlib.sha256(raw).hexdigest(),
        "dump_schema_version": dump_version,
        "validated_schema_version": SCHEMA_VERSION,
    }
    return result


def summarize(db: sqlite3.Connection, bills: list[Bill]) -> dict[str, Any]:
    candidates = [b for b in bills if b.status == "analyzed" and b.analysis is not None]
    records = [b.analysis for b in candidates if b.analysis is not None]
    by_key = {(b.term, b.number): b for b in bills}
    sent_cards = set(
        db.execute(
            "SELECT DISTINCT term, number FROM publications WHERE status='sent' "
            "AND kind IN ('new_bill', 'joint_bill') AND message_id IS NOT NULL"
        )
    )
    candidate_keys = {(b.term, b.number) for b in candidates}
    linked = []
    for bill in bills:
        if bill.status == "linked":
            target = by_key.get((bill.term, bill.linked_number or ""))
            linked.append(
                {
                    "term": bill.term,
                    "alias": bill.number,
                    "source": source(bill),
                    "target": bill.linked_number,
                    "target_status": target.status if target else None,
                    "target_is_candidate": bool(
                        target and (target.term, target.number) in candidate_keys
                    ),
                }
            )
    groups: list[set[tuple[int, str]]] = []
    for bill in bills:
        if not bill.summary.prints_considered_jointly:
            continue
        group = {(bill.term, bill.number)} | {
            (bill.term, number) for number in bill.summary.prints_considered_jointly
        }
        overlapping = [existing for existing in groups if group & existing]
        for existing in overlapping:
            group |= existing
            groups.remove(existing)
        groups.append(group)
    hidden = [
        {
            "term": b.term,
            "number": b.number,
            "status": b.status,
            "reason": b.last_error,
            "has_sent_telegram_card": (b.term, b.number) in sent_cards,
        }
        for b in bills
        if b.analysis is not None and b.status not in {"analyzed", "linked"}
    ]

    def counts(query: str) -> dict[str, int]:
        return {str(key): int(count) for key, count in db.execute(query)}

    def count(query: str) -> int:
        return int(db.execute(query).fetchone()[0])

    return {
        "validation": {
            "integrity_check": db.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_violations": len(db.execute("PRAGMA foreign_key_check").fetchall()),
            "validated_bill_rows": len(bills),
            "validated_analysis_records": sum(b.analysis is not None for b in bills),
            "by_status": dict(sorted(Counter(b.status for b in bills).items())),
        },
        "candidate_policy": {
            "rule": "status=analyzed and applied analysis exists; not a publication decision",
            "candidate_rows": len(candidates),
            "by_source": dict(sorted(Counter(source(b) for b in candidates).items())),
            "relevant": sum(r.analysis.relevant for r in records),
            "not_relevant": sum(not r.analysis.relevant for r in records),
            "score_at_least_3": sum(r.analysis.score >= 3 for r in records),
            "with_sent_telegram_card": len(candidate_keys & sent_cards),
            "blank_summary_or_practical_impact": sum(
                not r.analysis.summary.strip() or not r.analysis.practical_impact.strip()
                for r in records
            ),
            "blank_summary": sum(not r.analysis.summary.strip() for r in records),
            "blank_rationale": sum(not r.analysis.rationale.strip() for r in records),
            "relevant_with_blank_practical_impact": sum(
                r.analysis.relevant and not r.analysis.practical_impact.strip() for r in records
            ),
        },
        "identities": {
            "linked_rows": linked,
            "joint_groups": [
                [
                    {
                        "term": term,
                        "number": number,
                        "in_state": (term, number) in by_key,
                        "is_candidate": (term, number) in candidate_keys,
                    }
                    for term, number in sorted(group)
                ]
                for group in sorted(groups, key=lambda g: sorted(g))
            ],
            "linked_wykaz_rows": [
                {"term": b.term, "number": b.number, "wykaz_number": b.linked_wykaz_number}
                for b in bills
                if b.linked_wykaz_number
            ],
        },
        "history": {
            "status_changes": count("SELECT COUNT(*) FROM status_changes"),
            "matters_with_status_changes": count(
                "SELECT COUNT(*) FROM (SELECT DISTINCT term,number FROM status_changes)"
            ),
            "publication_statuses": counts(
                "SELECT status,COUNT(*) FROM publications GROUP BY status"
            ),
            "publication_kinds": counts("SELECT kind,COUNT(*) FROM publications GROUP BY kind"),
        },
        "batch": {
            "batches_by_status": counts("SELECT status,COUNT(*) FROM llm_batches GROUP BY status"),
            "intents_by_state": counts(
                "SELECT state,COUNT(*) FROM llm_batch_intents GROUP BY state"
            ),
            "items": count("SELECT COUNT(*) FROM llm_batch_items"),
            "unconsumed_items": count(
                "SELECT COUNT(*) FROM llm_batch_items WHERE consumed_at IS NULL"
            ),
            "unaccounted_results": count(
                "SELECT COUNT(*) FROM llm_batch_items "
                "WHERE result_json IS NOT NULL AND accounted_at IS NULL"
            ),
            "awaiting_batch_bills": sum(b.awaiting_batch_since is not None for b in bills),
            "ready_analysis_bills": sum(b.ready_analysis is not None for b in bills),
            "analysis_memos": count("SELECT COUNT(*) FROM analysis_memo"),
        },
        "freshness": {
            "candidates_without_analysis_source_checked_at": sum(
                r.source_checked_at is None for r in records
            ),
            "candidates_without_text_sha256": sum(not r.text_sha256 for r in records),
            "candidates_without_analysis_source_url": sum(not r.source_url for r in records),
            "candidates_with_observed_process": sum(
                b.observed_process is not None for b in candidates
            ),
            "source_checked_at_range": sorted(
                {r.source_checked_at.isoformat() for r in records if r.source_checked_at}
            ),
            "last_checked_at_is_not_aspect_freshness": True,
            "independent_observation_mode_available": False,
        },
        "preserved_non_candidates": hidden,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--committed-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.dump, args.commit, args.committed_at)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

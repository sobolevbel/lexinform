"""Report saved memo cleanup blockers and exact references from a SQLite snapshot."""

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path

from lexinform.adapters.memo_retention import memo_retention_cte
from tools.corpus_artifacts import write_json
from tools.sqlite_snapshot import open_snapshot, snapshot_files

_MEMOS = """
SELECT m.*, length(CAST(m.record_json AS BLOB)) AS bytes,
  EXISTS (SELECT 1 FROM computation_results r
    WHERE r.result_id=json_extract(m.record_json,'$.computation_id')) AS paid_id_ref
FROM memo_retention m
"""


def census(database: Path, memo_before: datetime) -> dict[str, object]:
    if memo_before.utcoffset() is None:
        raise ValueError("Memo cutoff requires a timezone")
    cutoff = memo_before.isoformat()
    exclusions: Counter[str] = Counter()
    orphan_exclusions: Counter[str] = Counter()
    orphan_references: Counter[str] = Counter()
    eligible = orphan_rows = orphan_record_bytes = 0
    with open_snapshot(database) as snapshot:
        owner_moves = (
            snapshot.connection.execute(
                "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='government_owner_moves'"
            ).fetchone()
            is not None
        )
        for row in snapshot.connection.execute(
            memo_retention_cte(owner_moves=owner_moves) + _MEMOS, {"cutoff": None}
        ):
            blockers = []
            used = row["last_used_at"]
            if used is None:
                blockers.append("last_used_at_missing")
            elif used >= cutoff:
                blockers.append("within_retention")
            if not row["has_bill"]:
                blockers.append(
                    "owner_unrecorded" if None in (row["term"], row["number"]) else "bill_missing"
                )
                orphan_rows += 1
                orphan_record_bytes += row["bytes"]
                orphan_references.update(
                    key
                    for key in ("intent_key_ref", "batch_item_key_ref", "paid_id_ref")
                    if row[key]
                )
            elif not row["settled"]:
                blockers.append("bill_unsettled")
            blockers.extend(
                key
                for key in (
                    "bill_work",
                    "document_work",
                    "publication",
                    "intent",
                    "batch_item",
                    "intent_key_ref",
                    "batch_item_key_ref",
                    "review_key_ref",
                )
                if row[key]
            )
            exclusions.update(blockers)
            if not row["has_bill"]:
                orphan_exclusions.update(blockers)
            eligible += not blockers
        if not snapshot.unchanged():
            raise ValueError("Database changed during memo retention census")
    return {
        "version": 1,
        "database_sha256": snapshot.sha256,
        "memo_before": cutoff,
        "eligible_rows": eligible,
        "exclusions": dict(exclusions),
        "ownerless_rows": orphan_rows,
        "ownerless_record_utf8_bytes": orphan_record_bytes,
        "ownerless_exclusions": dict(orphan_exclusions),
        "ownerless_recorded_refs": {
            key: orphan_references[key]
            for key in ("intent_key_ref", "batch_item_key_ref", "paid_id_ref")
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--memo-before", type=datetime.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    database = args.database.resolve()
    output = args.output.resolve()
    if output in snapshot_files(database):
        parser.error("Output must be separate from the SQLite snapshot and its sidecars")
    write_json(output, census(database, args.memo_before))


if __name__ == "__main__":
    main()

"""Report saved memo cleanup blockers and exact references from a SQLite snapshot."""

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path

from tools.corpus_artifacts import write_json
from tools.sqlite_snapshot import open_snapshot

_MEMOS = """
SELECT m.last_used_at, m.term, m.number, length(CAST(m.record_json AS BLOB)) AS bytes,
  b.term IS NOT NULL AS has_bill,
  b.status IN ('analyzed', 'linked', 'skipped_prefilter',
               'skipped_text_prefilter', 'skipped_closed') AS settled,
  b.awaiting_batch_since IS NOT NULL OR b.ready_analysis_json IS NOT NULL
    OR b.review_work_json IS NOT NULL AS bill_work,
  EXISTS (SELECT 1 FROM document_work w
    WHERE (w.term,w.number)=(m.term,m.number) AND w.state!='complete') AS document_work,
  EXISTS (SELECT 1 FROM publications p
    WHERE (p.term,p.number)=(m.term,m.number)
      AND p.status NOT IN ('sent','skipped')) AS publication,
  EXISTS (SELECT 1 FROM llm_batch_intents i
    WHERE (json_extract(i.request_json,'$.term'),json_extract(i.request_json,'$.number'))
      =(m.term,m.number)) AS intent,
  EXISTS (SELECT 1 FROM llm_batch_items i
    WHERE (i.term,i.number)=(m.term,m.number)
    AND (i.consumed_at IS NULL OR i.accounted_at IS NULL)) AS batch_item,
  EXISTS (SELECT 1 FROM llm_batch_intents i
    WHERE m.key IN (json_extract(i.meta_json,'$.memo_key'),
                    json_extract(i.meta_json,'$.delivery_key'))) AS intent_key_ref,
  EXISTS (SELECT 1 FROM llm_batch_items i
    WHERE (i.consumed_at IS NULL OR i.accounted_at IS NULL)
    AND m.key IN (json_extract(i.meta_json,'$.memo_key'),
                  json_extract(i.meta_json,'$.delivery_key'))) AS batch_item_key_ref,
  EXISTS (SELECT 1 FROM computation_results r
    WHERE r.result_id=json_extract(m.record_json,'$.computation_id')) AS paid_id_ref
FROM analysis_memo m LEFT JOIN bills b ON (b.term,b.number)=(m.term,m.number)
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
        for row in snapshot.connection.execute(_MEMOS):
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
                for key in ("bill_work", "document_work", "publication", "intent", "batch_item")
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
    if output in {Path(str(database) + suffix) for suffix in ("", "-wal", "-shm", "-journal")}:
        parser.error("Output must be separate from the SQLite snapshot and its sidecars")
    write_json(output, census(database, args.memo_before))


if __name__ == "__main__":
    main()

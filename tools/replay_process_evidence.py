"""Replay terminal decisions and processed fingerprints on the recorded Sejm corpus."""

import argparse
import datetime as dt
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from lexinform.adapters.sejm_api import parse_process_detail
from lexinform.models import Bill, BillStatus, ObservedProcess, is_over, next_phase


def replay(corpus: Path, destination: Path) -> None:
    now = dt.datetime(2026, 10, 3, tzinfo=dt.UTC)
    rows: list[dict[str, object]] = []
    coverage: Counter[int] = Counter()
    sources: dict[str, str] = {}
    for term in (8, 9, 10):
        path = corpus / f"sejm/term{term}/process_details.json.gz"
        raw = path.read_bytes()
        sources[str(path)] = hashlib.sha256(raw).hexdigest()
        records: dict[str, dict[str, Any]] = json.loads(gzip.decompress(raw))
        for number, record in records.items():
            if record.get("documentTypeEnum") != "BILL":
                continue
            detail = parse_process_detail(record)
            bill = Bill(
                summary=detail,
                stages=detail.stages,
                status=BillStatus.ANALYZED,
                first_seen_at=now,
                last_checked_at=now,
            )
            phase = next_phase(bill, today=now.date())
            observation = ObservedProcess(
                stages=bill.stages, passed=detail.passed, closure_date=detail.closure_date
            )
            rows.append(
                {
                    "term": term,
                    "number": number,
                    "over": is_over(bill, today=now.date()),
                    "phase": phase.key if phase else None,
                    "passed": detail.passed,
                    "closure": str(detail.closure_date),
                    "fingerprint": observation.fingerprint,
                }
            )
            coverage[term] += 1
    destination.write_text(
        json.dumps(
            {
                "coverage": dict(coverage),
                "sources": sources,
                "exclusions": "Non-BILL records; no act contents loaded.",
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print(json.dumps({"coverage": dict(coverage), "rows": len(rows)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    replay(args.corpus, args.destination)

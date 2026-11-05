"""Check one historical Sejm third-reading decision against a pinned official snapshot."""

import argparse
import gzip
import json
import re
from pathlib import Path

from lexinform.adapters.sejm_api import parse_process_detail
from lexinform.models import DecisionState, DocumentType, ReadingOutcome, reading_evidence
from tools.corpus_artifacts import digest, read_artifact, write_json


def audit_adoption(
    snapshot: Path, *, snapshot_sha256: str, term: int, number: str, stage_index: int
) -> dict[str, object]:
    if term < 1 or stage_index < 0 or re.fullmatch(r"[1-9][0-9]*", number) is None:
        raise ValueError(
            "Expected a positive term, numeric print number and zero-based stage index"
        )
    source = read_artifact(snapshot)
    if digest(source) != snapshot_sha256:
        raise ValueError("Snapshot SHA-256 differs from the expected pin")
    processes = json.loads(gzip.decompress(source))
    if not isinstance(processes, dict):
        raise ValueError("Snapshot must contain a process mapping")
    raw = processes.get(number)
    if not isinstance(raw, dict):
        raise ValueError("Selected process is unavailable")
    if (raw.get("term"), raw.get("number")) != (term, number) or type(raw.get("term")) is not int:
        raise ValueError("Selected process does not match the requested owner")
    stages = raw.get("stages")
    if not isinstance(stages, list) or stage_index >= len(stages):
        raise ValueError("Selected top-level stage is unavailable")
    detail = parse_process_detail(raw)
    if detail.document_type_enum is not DocumentType.BILL:
        raise ValueError("Selected process is outside the bill-only scope")
    stage = detail.stages[stage_index]
    decision = reading_evidence(stage)
    if decision.state is not DecisionState.KNOWN or decision.reading is not ReadingOutcome.PASSED:
        raise ValueError("Selected stage lacks a known Sejm third-reading adoption")
    if stage.date is None:
        raise ValueError("Selected adoption stage has no date")
    process_url = f"https://api.sejm.gov.pl/sejm/term{term}/processes/{number}"
    if stage.text_after3 != f"{process_url}/attachment/{number}_u3.pdf":
        raise ValueError("Selected stage lacks the exact same-owner adopted text locator")
    canonical_stage = json.dumps(
        stages[stage_index], sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    return {
        "version": 1,
        "scope": "Historical Sejm adoption of the whole bill at third reading",
        "term": term,
        "print_number": number,
        "process_url": process_url,
        "snapshot_sha256": snapshot_sha256,
        "stage_index": stage_index,
        "stage_sha256": digest(canonical_stage),
        "stage_date": stage.date.isoformat(),
        "reading_outcome": decision.reading.value,
        "adopted_text_locator": stage.text_after3,
        "exclusions": [
            "Adopted attachment bytes",
            "Individual provisions and their proposal lineage",
            "Later proceedings and legal force",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--snapshot-sha256", required=True)
    parser.add_argument("--term", type=int, required=True)
    parser.add_argument("--print-number", required=True)
    parser.add_argument("--stage-index", type=int, required=True, help="Zero-based top-level index")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = audit_adoption(
            args.snapshot,
            snapshot_sha256=args.snapshot_sha256,
            term=args.term,
            number=args.print_number,
            stage_index=args.stage_index,
        )
        write_json(args.output, report)
    except (OSError, ValueError, TypeError, AttributeError, KeyError, IndexError, EOFError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()

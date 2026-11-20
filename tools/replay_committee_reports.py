"""Compare declared committee amendment links with identity-checked catalogue attachments."""

import argparse
import gzip
import json
from collections import Counter
from pathlib import Path
from typing import Any

from lexinform.adapters.sejm_api import parse_print, parse_process_detail
from lexinform.committee_recommendation import committee_report_attachment
from lexinform.models import amendments_stage, flatten_stages
from tools.corpus_artifacts import write_json
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints


def report(corpus: Path, *, declared_only: bool = False) -> dict[str, Any]:
    observations: dict[str, Any] = {}
    counts: Counter[str] = Counter()
    for term in (8, 9, 10):
        folder = corpus / ("sejm/snapshots/2026-10-03/term10" if term == 10 else f"sejm/term{term}")
        prints = {
            str(item["number"]): parse_print(item, term=term)
            for item in json.loads((folder / "prints.json").read_text())
        }
        details = json.loads(gzip.decompress((folder / "process_details.json.gz").read_bytes()))
        for number, raw in details.items():
            if raw.get("documentTypeEnum") != "BILL":
                counts["non_bill_processes_excluded"] += 1
                continue
            detail = parse_process_detail(raw)
            for index, stage in enumerate(flatten_stages(detail.stages)):
                if stage.stage_type != "CommitteeReport" or amendments_stage([stage]) is None:
                    continue
                counts["reports"] += 1
                info = prints.get(stage.print_number or "")
                attachment = (
                    committee_report_attachment(stage, info, term=term, process_number=number)
                    if info is not None
                    else None
                )
                if info is None:
                    counts["catalogue_unavailable"] += 1
                elif attachment is None:
                    counts["replacement_unconfirmed"] += 1
                elif attachment.url != stage.report_file:
                    counts["catalogue_correction"] += 1
                else:
                    counts["declared_confirmed"] += 1
                observations[f"{term}/{number}/{index}/{stage.print_number}"] = {
                    "declared_url": stage.report_file,
                    "selected_url": attachment.url
                    if attachment is not None and not declared_only
                    else stage.report_file,
                    "attachments": [item.name for item in info.attachments] if info else None,
                }
    return {"observations": observations, "summary": dict(counts)}


def run(corpus: Path, output: Path, *, declared_only: bool = False) -> int:
    root, target = corpus.resolve(), output.resolve()
    if output.is_symlink() or target.exists() or target.is_relative_to(root):
        raise ValueError("Output must be new and outside the corpus")
    reads, before = CorpusReads(root), source_fingerprints()
    with reads.guard():
        reads.require(root / "INDEX.json")
        payload = report(root, declared_only=declared_only)
    changed = reads.changed_inputs()
    if changed or before != source_fingerprints():
        raise ValueError("Inputs or code changed during replay")
    write_json(
        target,
        payload
        | {
            "status": "recorded" if payload["observations"] else "not_run",
            "code_sha": code_version(),
            "source_sha256": before,
            "declared_only": declared_only,
            "read_inputs": reads.initial_sha256,
            "changed_inputs": changed,
            "missing_inputs": sorted(reads.missing),
            "exclusions": [
                "Metadata selection only; no downloads, text extraction, model or production IO.",
                "Only amendment-bearing committee reports; other reports keep their current path.",
                "Absent catalogues and contradictory identities retain the official stage link.",
                "Declared-only mode reproduces the prior direct reportFile selection.",
            ],
        },
    )
    return 0 if payload["observations"] else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--declared-only", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run(args.corpus, args.output, declared_only=args.declared_only))


if __name__ == "__main__":
    main()

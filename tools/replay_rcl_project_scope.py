"""Replay RCL project admission over saved listings, project metadata and one state witness."""

import argparse
import gzip
from collections import Counter
from pathlib import Path
from typing import Any

from lexinform.adapters.rcl_html import parse_list_page
from lexinform.models import RclProject, process_summary
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, source_fingerprints


def observe(project: RclProject) -> dict[str, object]:
    try:
        summary = process_summary(project, term=10)
    except ValueError as exc:
        if type(exc).__name__ != "UnsupportedRclProjectError":
            raise
        return {"state": "excluded", "title": project.title, "reason": str(exc)}
    return {"state": "admitted", "title": project.title, "document_type": summary.document_type}


def replay(corpus: Path, witness: Path) -> dict[str, Any]:
    root = corpus.resolve(strict=True)
    reads, before = CorpusReads(root), source_fingerprints()
    raw = read_artifact(witness)
    results = {"state_witness": observe(RclProject.model_validate_json(raw))}
    with reads.guard():
        reads.require(root / "INDEX.json")
        for path in sorted((root / "corpus").glob("[0-9]*.json")):
            results[str(path.relative_to(root))] = observe(
                RclProject.model_validate_json(path.read_bytes())
            )
        for path in sorted((root / "corpus/lists").glob("*.html.gz")):
            rows = parse_list_page(gzip.decompress(path.read_bytes()).decode()).rows
            for index, row in enumerate(rows):
                key = f"{path.relative_to(root)}/{index}/{row.id}"
                if row.created is None or row.modified is None:
                    results[key] = {"state": "unavailable", "title": row.title}
                    continue
                project = RclProject(
                    id=row.id,
                    title=row.title,
                    applicant=row.applicant,
                    created=row.created,
                    modified=row.modified,
                )
                results[key] = observe(project)
    if (
        reads.changed_inputs()
        or before != source_fingerprints()
        or digest(read_artifact(witness)) != digest(raw)
    ):
        raise ValueError("Project scope replay inputs or code changed")
    return {
        "status": "recorded",
        "scope": "Project-title admission; no raw document re-extraction",
        "counts": Counter(row["state"] for row in results.values()),
        "results": results,
        "read_inputs": reads.initial_sha256,
        "source_sha256": before,
        "witness": {"path": str(witness.resolve()), "sha256": digest(raw)},
        "missing_inputs": sorted(reads.missing),
        "complete_population": not reads.missing,
        "exclusions": [
            "Listings span two collection dates; no claim about current RCL coverage.",
            "Unrecognized titles retain prior admission; this does not prove their act type.",
            "The state witness was saved on 2026-11-13, not freshly fetched from RCL.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--witness", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_json(args.output, replay(args.corpus, args.witness))


if __name__ == "__main__":
    main()

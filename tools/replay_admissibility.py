"""Record operation-specific bill-read proposals on cached views without activating them."""

import argparse
import gzip
import zlib
from collections import Counter
from pathlib import Path
from typing import Any

from lexinform.document_admissibility import (
    ADMISSION_POLICY_VERSION,
    analysis_admission,
    prefilter_admission,
)
from lexinform.models import DocumentSelection
from lexinform.services.documents import MIN_TEXT_CHARS
from tools.corpus_artifacts import digest, write_json
from tools.corpus_health import TEXT_LIMIT
from tools.corpus_physical import TEXT_ROOTS
from tools.corpus_runtime import CorpusReads, source_fingerprints

PAGE_PROBES = (0, 1, 20)


def replay(corpus: Path) -> dict[str, Any]:
    root = corpus.resolve(strict=True)
    reads, before = CorpusReads(root), source_fingerprints()
    counts: Counter[str] = Counter()
    results: dict[str, Any] = {}
    selection = DocumentSelection()
    with reads.guard():
        reads.require(root / "INDEX.json")
        for family, folder in TEXT_ROOTS.items():
            for path in sorted((root / folder).rglob("*.gz")):
                identity = family + "/" + str(path.relative_to(root))
                try:
                    with gzip.open(path, "rb") as stream:
                        raw = stream.read(TEXT_LIMIT + 1)
                    if len(raw) > TEXT_LIMIT:
                        results[identity] = {"state": "cache_limit_exceeded"}
                        counts["cache_limit_exceeded"] += 1
                        continue
                    text = raw.decode("utf-8")
                except (OSError, EOFError, UnicodeError, zlib.error) as exc:
                    results[identity] = {
                        "state": "unreadable_cache",
                        "error_type": type(exc).__name__,
                    }
                    counts["unreadable_cache"] += 1
                    continue
                probes = {}
                for pages in PAGE_PROBES:
                    prefilter = prefilter_admission(
                        selection, text, pages=pages, min_chars=MIN_TEXT_CHARS
                    )
                    analysis = analysis_admission(
                        selection, text, pages=pages, min_chars=MIN_TEXT_CHARS
                    )
                    probes[str(pages)] = {"prefilter": prefilter, "analysis": analysis}
                    counts[f"prefilter/{prefilter}"] += 1
                    counts[f"analysis/{analysis}"] += 1
                counts["empty_cache" if not text.strip() else "readable_cache"] += 1
                results[identity] = {
                    "state": "empty_cache" if not text.strip() else "readable_cache",
                    "text_sha256": digest(raw),
                    "text_bytes": len(raw),
                    "page_probes": probes,
                }
    if reads.changed_inputs() or before != source_fingerprints():
        raise ValueError("Admissibility replay inputs or code changed")
    return {
        "status": "recorded" if results else "not_run",
        "policy_version": ADMISSION_POLICY_VERSION,
        "counts": dict(sorted(counts.items())),
        "results": results,
        "read_inputs": reads.initial_sha256,
        "source_sha256": before,
        "page_probes": list(PAGE_PROBES),
        "selection_state": selection.state,
        "scope": "Shadow bill-read proposals on cached views with counterfactual page probes.",
        "exclusions": [
            "No actual source-page count, selected state or complete historical input is inferred.",
            "Existing cached views are the population; missing caches are not invented.",
            "No legal accuracy or holdout verdict; runtime policy is not activated.",
            "No extraction, source download, model call, SQL write or corpus edit.",
        ],
    }


def run(corpus: Path, output: Path) -> int:
    if output.resolve().is_relative_to(corpus.resolve()) or output.exists() or output.is_symlink():
        raise ValueError("Output must be new and outside the corpus")
    result = replay(corpus)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, result)
    return 2 if result["status"] == "not_run" else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        code = run(args.corpus, args.output)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    raise SystemExit(code)


if __name__ == "__main__":
    main()

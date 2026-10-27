"""Record operation-specific read proposals on cached views without changing runtime policy."""

import argparse
import gzip
import zlib
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from lexinform.document_admissibility import (
    ADMISSION_POLICY_VERSION,
    REVIEW_ADMISSION_POLICY_VERSION,
    analysis_admission,
    prefilter_admission,
    review_admission,
)
from lexinform.models import DocumentSelection
from lexinform.models.reviews import DocumentRole as ReviewRole
from lexinform.services.documents import MIN_TEXT_CHARS
from tools.corpus_artifacts import digest, write_json
from tools.corpus_health import TEXT_LIMIT
from tools.corpus_physical import TEXT_ROOTS
from tools.corpus_runtime import CorpusReads, source_fingerprints

PAGE_PROBES = (0, 1, 20)
Operation = Literal["bill", "review"]
REVIEW_ROLES: tuple[ReviewRole, ...] = (
    "response",
    "position",
    "project_text",
    "comparison_text",
    "final_act",
    "commission_position",
    "commission_response",
    "commission_minutes",
    "commission_report",
)


def _bill_probes(selection: DocumentSelection, text: str, pages: int) -> dict[str, str]:
    return {
        "prefilter": prefilter_admission(selection, text, pages=pages, min_chars=MIN_TEXT_CHARS),
        "analysis": analysis_admission(selection, text, pages=pages, min_chars=MIN_TEXT_CHARS),
    }


def _review_probes(selection: DocumentSelection, text: str, pages: int) -> dict[str, str]:
    return {
        role: review_admission(
            selection, text, pages=pages, min_chars=MIN_TEXT_CHARS, oversize=False, role=role
        )
        for role in REVIEW_ROLES
    }


def replay(corpus: Path, *, operation: Operation = "bill") -> dict[str, Any]:
    root = corpus.resolve(strict=True)
    reads, before = CorpusReads(root), source_fingerprints()
    counts: Counter[str] = Counter()
    results: dict[str, Any] = {}
    selection = DocumentSelection()
    propose = _bill_probes if operation == "bill" else _review_probes
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
                    decisions = propose(selection, text, pages)
                    probes[str(pages)] = decisions
                    for name, decision in decisions.items():
                        counts[f"{name}/{decision}"] += 1
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
        "policy_version": ADMISSION_POLICY_VERSION
        if operation == "bill"
        else REVIEW_ADMISSION_POLICY_VERSION,
        "counts": dict(sorted(counts.items())),
        "results": results,
        "read_inputs": reads.initial_sha256,
        "source_sha256": before,
        "page_probes": list(PAGE_PROBES),
        "selection_state": selection.state,
        **({"role_probes": list(REVIEW_ROLES)} if operation == "review" else {}),
        "scope": f"Shadow {operation}-read proposals on cached views with counterfactual probes.",
        "exclusions": [
            "No actual source-page count, selected state or complete historical input is inferred.",
            "Existing cached views are the population; missing caches are not invented.",
            "No legal accuracy or holdout verdict; this replay does not change runtime policy.",
            "No extraction, source download, model call, SQL write or corpus edit.",
            *(
                ["Role probes are counterfactual, not inferred source roles or legal completeness."]
                if operation == "review"
                else []
            ),
        ],
    }


def run(corpus: Path, output: Path, *, operation: Operation = "bill") -> int:
    if output.resolve().is_relative_to(corpus.resolve()) or output.exists() or output.is_symlink():
        raise ValueError("Output must be new and outside the corpus")
    result = replay(corpus, operation=operation)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, result)
    return 2 if result["status"] == "not_run" else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--operation", choices=("bill", "review"), default="bill")
    args = parser.parse_args()
    try:
        code = run(args.corpus, args.output, operation=args.operation)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    raise SystemExit(code)


if __name__ == "__main__":
    main()

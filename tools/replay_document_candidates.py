"""Replay RCL candidate validation on saved extractions, without network or model calls."""

import argparse
import datetime as dt
import json
import logging
from collections import Counter
from pathlib import Path

from lexinform.sections import document_kind
from lexinform.services.documents import TextLoader
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_rcl import CachedExtractor as CachedExtractor
from tools.corpus_rcl import RequiredCachedExtractor, projects, stage_cases
from tools.corpus_rcl import document_id as document_id
from tools.corpus_runtime import CorpusReads, source_fingerprints


def replay(
    corpus: Path, *, derivatives: Path | None = None, derivatives_sha256: str | None = None
) -> dict[str, object]:
    root = corpus.resolve(strict=True)
    reads, before = CorpusReads(root), source_fingerprints()
    extractor = RequiredCachedExtractor(
        root, reads, derivatives=derivatives, derivatives_sha256=derivatives_sha256
    )
    loader = TextLoader(
        {"legislacja.rcl.gov.pl": document_id},
        extractor,
        max_bytes=32_000_000,
        cache_size=256,
    )
    results: dict[str, object] = {}
    counts: Counter[str] = Counter()
    now = dt.datetime(2026, 10, 17, tzinfo=dt.UTC)
    with reads.guard():
        for path, project in projects(root):
            for case in stage_cases(path, project, as_of=now):
                if case.document is None:
                    counts["no_candidate"] += 1
                    results[case.identity] = {"state": "no_candidate"}
                    continue
                selected, file = loader.select_document(case.document)
                state = (
                    "waiting"
                    if file.text is None and file.selection.state == "unverified"
                    else file.selection.state
                )
                counts[state] += 1
                results[case.identity] = {
                    "state": state,
                    "url": selected.url,
                    "kind": document_kind(file.text or ""),
                    "chars": len(file.text or ""),
                    "reasons": file.selection.rejected,
                    "extras": selected.extra_urls,
                    "amendments": selected.amendment_urls,
                    "normative": selected.normative_urls,
                }
    external = extractor.derived.inputs if extractor.derived is not None else {}
    if (
        reads.changed_inputs()
        or before != source_fingerprints()
        or any(digest(read_artifact(Path(path))) != sha for path, sha in external.items())
    ):
        raise ValueError("Candidate replay inputs or code changed")
    return {
        "status": "recorded",
        "scope": "Cached candidate observations; missing sources remain outcomes",
        "counts": counts,
        "results": results,
        "read_inputs": reads.initial_sha256,
        "source_sha256": before,
        "derivative_inputs": external,
        "missing_inputs": sorted(reads.missing),
        "complete_population": not reads.missing,
        "exclusions": ["Cached views do not establish raw bundle, scan or legal completeness."],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path, nargs="?")
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--corpus", dest="corpus_option", type=Path)
    parser.add_argument("--output", dest="output_option", type=Path)
    parser.add_argument("--derivatives", type=Path)
    parser.add_argument("--derivatives-sha256")
    args = parser.parse_args()
    corpus, output = args.corpus_option or args.corpus, args.output_option or args.output
    if corpus is None or output is None:
        parser.error("Corpus and a new output path are required")
    if output.resolve().is_relative_to(corpus.resolve()) or output.exists() or output.is_symlink():
        parser.error("Output must be new and outside the corpus")
    logging.basicConfig(level=logging.ERROR)
    result = replay(
        corpus, derivatives=args.derivatives, derivatives_sha256=args.derivatives_sha256
    )
    write_json(output, result)
    print(json.dumps(result["counts"]))


if __name__ == "__main__":
    main()

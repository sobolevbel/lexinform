"""Replay RCL candidate validation on saved extractions, without network or model calls."""

import argparse
import datetime as dt
import json
import logging
from collections import Counter
from pathlib import Path

from lexinform.sections import document_kind
from lexinform.services.documents import TextLoader
from tools.corpus_rcl import CachedExtractor as CachedExtractor
from tools.corpus_rcl import document_id as document_id
from tools.corpus_rcl import projects, stage_cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    loader = TextLoader(
        {"legislacja.rcl.gov.pl": document_id},
        CachedExtractor(args.corpus),
        max_bytes=32_000_000,
        cache_size=256,
    )
    results: dict[str, object] = {}
    counts: Counter[str] = Counter()
    now = dt.datetime(2026, 10, 17, tzinfo=dt.UTC)
    for path, project in projects(args.corpus):
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
    with args.output.open("x") as stream:
        json.dump(
            {"counts": counts, "results": results},
            stream,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    print(json.dumps(counts))


if __name__ == "__main__":
    main()

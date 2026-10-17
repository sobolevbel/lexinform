"""Replay RCL candidate validation on saved extractions, without network or model calls."""

import argparse
import datetime as dt
import gzip
import json
import logging
import re
from collections import Counter
from pathlib import Path

from lexinform.models import Bill, BillStatus, RclProject, process_summary
from lexinform.sections import document_kind
from lexinform.services.documents import TextLoader
from lexinform.services.sources import RclTextSource


class CachedExtractor:
    def __init__(self, corpus: Path) -> None:
        self.corpus = corpus

    def extract(self, data: bytes) -> str:
        path = self.corpus / f"documents/text/{data.decode()}.txt.gz"
        return gzip.decompress(path.read_bytes()).decode() if path.exists() else ""

    def pages(self, data: bytes) -> int:
        return 0

    def select_pages(self, data: bytes, *, first: int, count: int) -> bytes:
        raise AssertionError("Cached text replay cannot validate scanned pages")


def document_id(url: str, *, max_bytes: int | None = None) -> bytes:
    match = re.search(r"dokument(\d+)", url)
    if match is None:
        raise ValueError("URL absent from the RCL document corpus")
    return match[1].encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    source = RclTextSource()
    loader = TextLoader(
        {"legislacja.rcl.gov.pl": document_id},
        CachedExtractor(args.corpus),
        max_bytes=32_000_000,
        cache_size=256,
    )
    results: dict[str, object] = {}
    counts: Counter[str] = Counter()
    now = dt.datetime(2026, 10, 17, tzinfo=dt.UTC)
    for path in sorted((args.corpus / "corpus").glob("1*.json")):
        project = RclProject.model_validate_json(path.read_bytes())
        for stage in project.reached_stages:
            snapshot = project.model_copy(
                update={
                    "stages": tuple(st for st in project.stages if st.number <= stage.number),
                }
            )
            bill = Bill(
                summary=process_summary(snapshot, term=10),
                rcl=snapshot,
                status=BillStatus.ANALYSIS_PENDING,
                first_seen_at=now,
                last_checked_at=now,
            )
            located = source.locate(bill)
            key = f"{project.id}/{stage.number}"
            if located.document is None:
                counts["no_candidate"] += 1
                results[key] = {"state": "no_candidate"}
                continue
            selected, file = loader.select_document(located.document)
            state = (
                "waiting"
                if file.text is None and file.selection.state == "unverified"
                else file.selection.state
            )
            counts[state] += 1
            results[key] = {
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

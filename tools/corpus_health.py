"""Report indexed RCL file availability and candidate gaps without downloads or corpus writes."""

import argparse
import datetime as dt
import gzip
import json
import re
import zlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from lexinform.models import Bill, BillStatus, RclProject, process_summary
from lexinform.services.sources import RclTextSource
from tools.corpus_check import source_fingerprints
from tools.corpus_external import CorpusReads
from tools.replay_input_coverage import digest

TEXT_LIMIT = 32_000_000
DOCUMENT_URL = re.compile(r"dokument(\d+)\.[A-Za-z0-9]+(?:$|[?#])")


def local_path(root: Path, relative: str) -> Path:
    path = root / relative
    if path.resolve() != path or not path.resolve().is_relative_to(root):
        raise ValueError(f"Corpus path must stay inside its root without symlinks: {relative}")
    return path


def amendment_candidates(root: Path) -> tuple[dict[str, list[dict[str, str]]], dict[str, int]]:
    references: dict[str, list[dict[str, str]]] = defaultdict(list)
    counts: Counter[str] = Counter()
    source = RclTextSource()
    now = dt.datetime(2026, 10, 25, tzinfo=dt.UTC)
    for path in sorted(local_path(root, "corpus").glob("1*.json")):
        project = RclProject.model_validate_json(path.read_bytes())
        counts["projects"] += 1
        for stage in project.reached_stages:
            counts["stages"] += 1
            snapshot = project.model_copy(
                update={"stages": tuple(s for s in project.stages if s.number <= stage.number)}
            )
            bill = Bill(
                summary=process_summary(snapshot, term=10),
                rcl=snapshot,
                status=BillStatus.ANALYSIS_PENDING,
                first_seen_at=now,
                last_checked_at=now,
            )
            document = source.locate(bill).document
            if document is None or not document.amendment_urls:
                continue
            counts["cases"] += 1
            pending: list[tuple[str, Any]] = [("document", document.model_dump(mode="json"))]
            while pending:
                field, value = pending.pop()
                if isinstance(value, dict):
                    pending.extend((f"{field}.{key}", item) for key, item in value.items())
                elif isinstance(value, list):
                    pending.extend((f"{field}[{i}]", item) for i, item in enumerate(value))
                elif isinstance(value, str) and (match := DOCUMENT_URL.search(value)):
                    references[match[1]].append(
                        {"case": f"{project.id}/{stage.number}", "field": field, "url": value}
                    )
    return references, dict(counts)


def text_state(path: Path, *, check_text: bool) -> str:
    if not path.is_file():
        return "missing"
    if not check_text:
        return "present_unchecked"
    try:
        with gzip.open(path, "rb") as stream:
            data = stream.read(TEXT_LIMIT + 1)
        if len(data) > TEXT_LIMIT:
            return "limit_exceeded"
        return "readable" if data.decode("utf-8").strip() else "empty"
    except OSError, EOFError, UnicodeError, zlib.error:
        return "unreadable"


def inspect(corpus: Path, *, check_text: bool = False) -> dict[str, Any]:
    root = corpus.resolve(strict=True)
    reads = CorpusReads(root)
    before = source_fingerprints()
    records: dict[str, Any] = {}
    actions: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    with reads.guard():
        index = json.loads(local_path(root, "documents/index.json").read_bytes())
        references, cohort = amendment_candidates(root)
        for identity in sorted(set(index) | set(references), key=int):
            row = index.get(identity, {})
            extension = row.get("extension", "")
            if not identity.isdecimal() or (
                extension and not re.fullmatch(r"[A-Za-z0-9]+", extension)
            ):
                raise ValueError("Document identity or extension is not a local filename")
            raw = (
                [local_path(root, f"documents/files/{identity}.{extension}")]
                if extension
                else sorted(local_path(root, "documents/files").glob(f"{identity}.*"))
            )
            for path in raw:
                local_path(root, str(path.relative_to(root)))
            present = [path for path in raw if path.is_file()]
            raw_state = "present" if len(present) == 1 else "multiple" if present else "missing"
            text = local_path(root, f"documents/text/{identity}.txt.gz")
            derived = text_state(text, check_text=check_text)
            related = sorted(
                references.get(identity, []), key=lambda ref: (ref["case"], ref["field"])
            )
            urls = sorted(
                {ref["url"] for ref in related} | ({row["url"]} if row.get("url") else set())
            )
            tasks = []
            if raw_state == "missing":
                tasks.append(
                    ("fetch_source", True, "Raw source absent; cached text cannot recreate it")
                )
            elif raw_state == "multiple":
                tasks.append(("review_raw_aliases", False, "Multiple physical files for one ID"))
            if derived in ("missing", "unreadable") and raw_state == "present":
                tasks.append(
                    ("reextract_text", False, f"Cached extraction is {derived}; source available")
                )
            elif derived == "empty":
                tasks.append(
                    ("review_empty_text", False, "Empty text does not prove a scan or irrelevance")
                )
            elif derived == "limit_exceeded":
                tasks.append(
                    ("review_text_size", False, "Text exceeds the bounded health inspection")
                )
            if row.get("extract_error"):
                tasks.append(("review_extractor_error", False, str(row["extract_error"])))
            if not row:
                tasks.append(
                    ("index_document", False, "Candidate is absent from documents/index.json")
                )
            for action, network, reason in tasks:
                actions.append(
                    {
                        "id": identity,
                        "action": action,
                        "network_required": network,
                        "reason": reason,
                        "urls": urls,
                        "references": related,
                    }
                )
            counts[f"raw_{raw_state}"] += 1
            counts[f"text_{derived}"] += 1
            records[identity] = {
                "indexed": bool(row),
                "raw_state": raw_state,
                "text_state": derived,
                "raw_paths": [str(path.relative_to(root)) for path in present],
                "text_path": str(text.relative_to(root)),
                "references": related,
                "urls": urls,
            }
    changed = [
        name
        for name, sha in reads.initial_sha256.items()
        if not (root / name).is_file() or digest((root / name).read_bytes()) != sha
    ]
    if changed or before != source_fingerprints():
        raise ValueError("Corpus inputs or code changed during health inspection")
    return {
        "version": 1,
        "status": "recorded",
        "scope": "Indexed RCL files plus self-amendment candidates",
        "check_text": check_text,
        "counts": dict(counts),
        "indexed_documents": len(index),
        "candidate_cohort": cohort,
        "records": records,
        "actions": actions,
        "action_counts": dict(Counter(task["action"] for task in actions)),
        "read_inputs": reads.initial_sha256,
        "source_sha256": before,
        "exclusions": [
            "Raw availability is not byte integrity, legal role or completeness.",
            "The candidate cohort follows current code over saved metadata, not a legal oracle.",
            "Only RCL document files and the self-amendment cohort are inspected.",
            "Missing candidate files are not a mandate to download every RCL catalog entry.",
            "No downloads, OCR, model calls, extraction or production state changes are performed.",
        ],
    }


def run(corpus: Path, output: Path, *, check_text: bool = False) -> None:
    target = output.resolve()
    if output.is_symlink() or target.exists() or target.is_relative_to(corpus.resolve()):
        raise ValueError("Output must be new and outside the corpus")
    result = inspect(corpus, check_text=check_text)
    with target.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps({"counts": result["counts"], "actions": result["action_counts"]}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-text", action="store_true")
    args = parser.parse_args()
    run(args.corpus, args.output, check_text=args.check_text)


if __name__ == "__main__":
    main()

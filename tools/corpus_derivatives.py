"""Re-extract pinned empty caches into a new generation while preserving corpus inputs."""

import argparse
import gzip
import json
import logging
import sys
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from typing import Any

from lexinform.sections import document_kind
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_extraction import MAX_BYTES as MAX_BYTES
from tools.corpus_extraction import OPTIONS as OPTIONS
from tools.corpus_extraction import bounded_reader
from tools.corpus_health import local_path
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints


def run(
    corpus: Path,
    health: Path,
    health_sha256: str,
    output: Path,
    *,
    index_snapshot: Path | None = None,
) -> int:
    root, target = corpus.resolve(strict=True), output.resolve()
    if output.is_symlink() or target.exists() or target.is_relative_to(root):
        raise ValueError("Output must be new and outside the corpus")
    payload = read_artifact(health)
    if digest(payload) != health_sha256:
        raise ValueError("Health report SHA mismatch")
    baseline = json.loads(payload)
    if baseline.get("status") != "recorded" or baseline.get("check_text") is not True:
        raise ValueError("A completed checked-text health inventory is required")
    cohort = {key: row for key, row in baseline["records"].items() if row["text_state"] == "empty"}
    if not cohort:
        raise ValueError("Empty-text cohort must not be empty")
    reads, before = CorpusReads(root), source_fingerprints()
    if index_snapshot is None:
        with reads.guard():
            index_bytes = local_path(root, "documents/index.json").read_bytes()
    else:
        index_bytes = read_artifact(index_snapshot)
    if digest(index_bytes) != baseline["read_inputs"]["documents/index.json"]:
        raise ValueError("Document index changed since health inspection")
    index = json.loads(index_bytes)
    reader = bounded_reader()
    generation = target.with_suffix("")
    generation.mkdir()
    rows: dict[str, Any] = {}
    objects: dict[str, str] = {}
    for number, (identity, item) in enumerate(
        sorted(cohort.items(), key=lambda pair: int(pair[0])), 1
    ):
        row: dict[str, Any] = {"state": "not_run", "extraction": None}
        compressed = None
        with reads.guard():
            cached = local_path(root, item["text_path"]).read_bytes()
            if digest(cached) != baseline["read_inputs"][item["text_path"]]:
                raise ValueError(f"Empty cache changed since health inspection: {identity}")
            old = gzip.decompress(cached).decode()
            if old.strip():
                raise ValueError("Pinned empty cache contains text")
            paths = item["raw_paths"]
            if len(paths) != 1:
                row["reason"] = "Raw source is missing or ambiguous"
            else:
                path = local_path(root, paths[0])
                row.update(
                    raw_path=paths[0],
                    source_url=index[identity].get("url"),
                    old_text_sha256=digest(old.encode()),
                )
                if path.stat().st_size > MAX_BYTES:
                    row["reason"] = "Raw source exceeds the 32 MB replay limit"
                else:
                    data = path.read_bytes()
                    row["raw_sha256"] = digest(data)
                    try:
                        extracted = reader.extract_document(data)
                        text = extracted.text
                        row.update(
                            state="recovered" if text.strip() else "still_empty",
                            chars=len(text),
                            text_sha256=digest(text.encode()),
                            kind=document_kind(text),
                            selection=extracted.selection.without_text().model_dump(mode="json"),
                        )
                        compressed = gzip.compress(text.encode(), mtime=0)
                    except Exception as exc:
                        row.update(state="failed", error=f"{type(exc).__name__}: {exc}")
        if compressed is not None:
            text_sha = row["text_sha256"]
            if text_sha not in objects:
                path = generation / f"{text_sha}.txt.gz"
                with path.open("xb") as stream:
                    stream.write(compressed)
                objects[text_sha] = str(path.relative_to(target.parent))
            row["extraction"] = objects[text_sha]
        rows[identity] = row
        if number % 10 == 0 or number == len(cohort):
            print(f"empty-text replay {number}/{len(cohort)}", file=sys.stderr)
    changed = reads.changed_inputs()
    changed_code = before != source_fingerprints()
    counts = dict(Counter(row["state"] for row in rows.values()))
    failed = bool(changed or changed_code or counts.get("failed"))
    result = {
        "version": 1,
        "status": "failed" if failed else "recorded",
        "scope": "New bounded derivatives of empty RCL caches; old corpus bytes preserved",
        "code_sha": code_version(),
        "source_sha256": before,
        "health_sha256": health_sha256,
        "options": OPTIONS,
        "index_snapshot_sha256": digest(index_bytes),
        "libraries": {name: version(name) for name in ("pypdf", "inflate64", "olefile")},
        "observations": rows,
        "counts": counts,
        "unique_text_objects": len(objects),
        "read_inputs": reads.initial_sha256,
        "changed_inputs": changed,
        "changed_code": changed_code,
        "exclusions": [
            "No old cache or index updates; no downloads, OCR, model calls or production state.",
            "Recovered text and automatic roles do not prove legal completeness or relevance.",
            "Inputs above 32 MB remain explicit exclusions; pages without text are not OCRed.",
        ],
    }
    write_json(target, result)
    return int(failed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--health", type=Path, required=True)
    parser.add_argument("--health-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--index-snapshot", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    raise SystemExit(
        run(
            args.corpus,
            args.health,
            args.health_sha256,
            args.output,
            index_snapshot=args.index_snapshot,
        )
    )


if __name__ == "__main__":
    main()

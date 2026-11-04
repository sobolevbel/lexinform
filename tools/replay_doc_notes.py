"""Replay pinned DOC note declarations offline without changing text or retaining source bodies."""

import argparse
import io
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import olefile
from pydantic import BaseModel, Field

from lexinform.adapters.doc_notes import read_doc_note_links
from lexinform.adapters.doc_text import table_stream_name
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints


class DocInput(BaseModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    extension: str


class DocManifest(BaseModel):
    index_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cases: tuple[DocInput, ...] = Field(min_length=1)


def inspect(data: bytes, *, max_notes: int) -> dict[str, Any]:
    if not olefile.isOleFile(data):
        return {"status": "unavailable", "reason": "not_ole"}
    try:
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            word = ole.openstream("WordDocument").read()
            table = ole.openstream(table_stream_name(word)).read()
        links = read_doc_note_links(word, table, max_notes=max_notes)
    except (OSError, ValueError) as exc:
        return {"status": "unavailable", "reason": str(exc), "error_type": type(exc).__name__}
    return {"status": "declared_cp_only", **asdict(links)}


def run(corpus: Path, manifest_path: Path, output: Path, *, max_notes: int = 256) -> dict[str, Any]:
    corpus = corpus.resolve()
    output = output.resolve()
    if output.is_relative_to(corpus):
        raise ValueError("Replay output must be outside the read-only corpus")
    if max_notes < 0 or max_notes > 256:
        raise ValueError("Replay max_notes must be between 0 and 256")
    manifest_bytes = read_artifact(manifest_path)
    manifest = DocManifest.model_validate_json(manifest_bytes)
    reads = CorpusReads(corpus)
    results: dict[str, dict[str, Any]] = {}
    paths: list[dict[str, str]] = []
    with reads.guard():
        index = (corpus / "INDEX.json").read_bytes()
        if digest(index) != manifest.index_sha256:
            raise ValueError("Corpus INDEX differs from the pinned manifest")
        for item in manifest.cases:
            if item.extension != ".doc":
                continue
            data = (corpus / item.path).read_bytes()
            if digest(data) != item.sha256:
                raise ValueError(f"Raw input SHA differs: {item.path}")
            if item.sha256 not in results:
                results[item.sha256] = inspect(data, max_notes=max_notes)
            paths.append({"path": item.path, "sha256": item.sha256})
    changed = reads.changed_inputs()
    if changed:
        raise ValueError(f"Corpus inputs changed during replay: {len(changed)}")
    counts = Counter(record["status"] for record in results.values())
    links = [link for record in results.values() for link in record.get("links", [])]
    summary = {
        "paths": len(paths),
        "unique_inputs": len(results),
        "status_counts": dict(counts),
        "note_bearing_unique": sum(bool(record.get("links")) for record in results.values()),
        "links_by_kind": dict(Counter(link["kind"] for link in links)),
        "changed_inputs": changed,
        "verified_input_paths": len(reads.paths),
    }
    report = {
        "version": 1,
        "scope": "table-declared CP links only; no text/CFSpec validation",
        "manifest_sha256": digest(manifest_bytes),
        "index_sha256": manifest.index_sha256,
        "code_version": code_version(),
        "source_fingerprints": source_fingerprints(),
        "max_notes": max_notes,
        "summary": summary,
        "paths": paths,
        "observations": results,
        "exclusions": [
            "Non-DOC inputs",
            "Nested archive leaves absent from the manifest",
            "Character/CFSpec checks",
            "Independent legal labels",
            "Default runtime capture",
        ],
    }
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "report.json", report)
    write_json(output / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-notes", type=int, default=256)
    args = parser.parse_args()
    print(json.dumps(run(args.corpus, args.manifest, args.output, max_notes=args.max_notes)))


if __name__ == "__main__":
    main()

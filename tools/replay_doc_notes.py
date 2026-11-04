"""Replay pinned DOC note declarations offline without changing text or retaining source bodies."""

import argparse
import io
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

import olefile
from pydantic import BaseModel, Field

from lexinform.adapters.doc_notes import read_doc_note_links
from lexinform.adapters.doc_text import DocTextExtractor, table_stream_name
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.document_candidates import select_document_parts
from lexinform.extraction_records import summarize_doc_notes
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints


class DocInput(BaseModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    extension: str


class TextObservation(BaseModel):
    legacy_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class TextReference(BaseModel):
    observations: dict[str, TextObservation]


class DocManifest(BaseModel):
    index_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cases: tuple[DocInput, ...] = Field(min_length=1)


def physical_snapshot(data: bytes) -> dict[str, Any]:
    reader = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    baseline = reader.extract_parts(data)
    linked = reader.extract_parts_with_doc_notes(data)
    if linked.legacy != baseline:
        raise ValueError("DOC sidecars changed the legacy physical extraction")
    selected = select_document_parts(linked.legacy, max_part_chars=None)
    if selected != reader.extract_document(data):
        raise ValueError("DOC sidecars changed package selection")
    observed = reader.extract_document_with_provenance(data)
    if observed.document != selected or any(
        part.doc_notes for part in observed.result.selected_parts
    ):
        raise ValueError("Default provenance or selected text changed unexpectedly")
    enriched = summarize_doc_notes(observed.result, linked)
    without_notes = enriched.model_dump(mode="json")
    for part in without_notes["selected_parts"]:
        part.pop("doc_notes", None)
    if without_notes != observed.result.model_dump(mode="json"):
        raise ValueError("DOC summaries changed existing extraction fields")
    summaries = [part.doc_notes for part in enriched.selected_parts if part.doc_notes is not None]
    return {
        "physical_sha256": digest(baseline.model_dump_json().encode()),
        "selection_sha256": digest(selected.selection.model_dump_json().encode()),
        "selected_text_sha256": digest(selected.text.encode()),
        "physical_manifest_sha256": enriched.physical_manifest_sha256,
        "captured_leaves": len(linked.doc_notes),
        "checked_leaves": sum(part.extraction.state == "checked" for part in linked.doc_notes),
        "captured_links": sum(len(part.extraction.links) for part in linked.doc_notes),
        "retained_links": sum(len(notes.links) for notes in summaries),
        "retained_note_bytes": sum(len(notes.model_dump_json().encode()) for notes in summaries),
        "total_summary_bytes": len(enriched.model_dump_json().encode()),
        "retained_coverage": [notes.coverage for notes in summaries],
        "default_note_writer": False,
    }


def inspect(data: bytes, *, max_notes: int, source_characters: bool = False) -> dict[str, Any]:
    if source_characters:
        if max_notes != 256:
            raise ValueError("Source qualification uses the producer's fixed 256-note limit")
        reader = DocTextExtractor()
        with patch.object(olefile, "OleFileIO", wraps=olefile.OleFileIO) as opened:
            result = reader.extract_with_notes(data)
        if opened.call_count != int(olefile.isOleFile(io.BytesIO(data))):
            raise ValueError("Opt-in extraction did not use exactly one OLE container read")
        text = result.result
        if result.input_sha256 != digest(data) or text.legacy_text != reader.extract(data):
            raise ValueError("Opt-in note capture changed input identity or default text")
        return {
            **(asdict(text.links) if text.links is not None else {}),
            "status": text.validation or "unavailable",
            "input_sha256": result.input_sha256,
            "ole_opens": opened.call_count,
            "legacy_text_sha256": digest(text.legacy_text.encode()),
            "unavailable_reason": text.unavailable_reason,
        }
    if not olefile.isOleFile(io.BytesIO(data)):
        return {"status": "unavailable", "reason": "not_ole"}
    try:
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            word = ole.openstream("WordDocument").read()
            table = ole.openstream(table_stream_name(word)).read()
        links = read_doc_note_links(word, table, max_notes=max_notes)
    except (OSError, ValueError) as exc:
        return {"status": "unavailable", "reason": str(exc), "error_type": type(exc).__name__}
    return {"status": "declared_cp_only", **asdict(links)}


def run(
    corpus: Path,
    manifest_path: Path,
    output: Path,
    *,
    max_notes: int = 256,
    source_characters: bool = False,
    physical_binding: bool = False,
    reference: Path | None = None,
    reference_sha256: str | None = None,
) -> dict[str, Any]:
    corpus = corpus.resolve()
    output = output.resolve()
    if output.is_relative_to(corpus):
        raise ValueError("Replay output must be outside the read-only corpus")
    if max_notes < 0 or max_notes > 256:
        raise ValueError("Replay max_notes must be between 0 and 256")
    if physical_binding and not source_characters:
        raise ValueError("Physical DOC binding requires source-character qualification")
    prior: TextReference | None = None
    if reference is not None or reference_sha256 is not None:
        if reference is None or reference_sha256 is None or not source_characters:
            raise ValueError("A pinned text reference requires source-character qualification")
        reference_bytes = read_artifact(reference)
        if digest(reference_bytes) != reference_sha256:
            raise ValueError("Text reference SHA differs")
        prior = TextReference.model_validate_json(reference_bytes)
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
            reads.require(corpus / item.path)
            data = (corpus / item.path).read_bytes()
            if digest(data) != item.sha256:
                raise ValueError(f"Raw input SHA differs: {item.path}")
            if item.sha256 not in results:
                results[item.sha256] = inspect(
                    data, max_notes=max_notes, source_characters=source_characters
                )
                if physical_binding:
                    results[item.sha256]["physical_binding"] = physical_snapshot(data)
            paths.append({"path": item.path, "sha256": item.sha256})
    if prior is not None:
        if prior.observations.keys() != results.keys():
            raise ValueError("Reference and replay populations differ")
        if any(
            record["legacy_text_sha256"] != prior.observations[sha].legacy_text_sha256
            for sha, record in results.items()
        ):
            raise ValueError("Legacy text differs from the pinned reference")
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
        "scope": (
            "Source characters checked; CFSpec/emitted offsets unknown"
            if source_characters
            else "table-declared CP links only; no text/CFSpec validation"
        ),
        "manifest_sha256": digest(manifest_bytes),
        "index_sha256": manifest.index_sha256,
        "code_version": code_version(),
        "source_fingerprints": source_fingerprints(),
        "physical_binding": physical_binding,
        "reference_sha256": reference_sha256,
        "max_notes": max_notes,
        "summary": summary,
        "paths": paths,
        "observations": results,
        "exclusions": [
            "Non-DOC inputs",
            "Nested archive leaves absent from the manifest",
            "CFSpec/emitted offset checks" if source_characters else "Character/CFSpec checks",
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
    parser.add_argument("--source-characters", action="store_true")
    parser.add_argument("--physical-binding", action="store_true")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--reference-sha256")
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.corpus,
                args.manifest,
                args.output,
                max_notes=args.max_notes,
                source_characters=args.source_characters,
                physical_binding=args.physical_binding,
                reference=args.reference,
                reference_sha256=args.reference_sha256,
            )
        )
    )


if __name__ == "__main__":
    main()

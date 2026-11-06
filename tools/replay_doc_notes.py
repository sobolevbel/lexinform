"""Replay pinned DOC note declarations offline without changing text or retaining source bodies."""

import argparse
import gzip
import io
import json
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal
from unittest.mock import patch

import olefile
from pydantic import BaseModel, Field

from lexinform.adapters.doc_notes import DocNoteLink, DocNoteLinks, read_doc_note_links
from lexinform.adapters.doc_text import (
    DocTextExtractor,
    _cleanup_positions,
    _read_pieces,
    _row_end_marks,
    _spans,
    _streams,
    _word_text,
    table_stream_name,
    word_text,
)
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.document_candidates import select_document_parts
from lexinform.extraction_records import summarize_doc_note_projections, summarize_doc_notes
from lexinform.models.doc_notes import MAX_DOC_NOTE_SUMMARY_BYTES
from lexinform.models.extractions import MAX_EXTRACTION_METADATA_BYTES, ExtractionResult
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


PHYSICAL_SEMANTIC_FIELDS = (
    "physical_sha256",
    "selection_sha256",
    "selected_text_sha256",
    "physical_manifest_sha256",
)


class PhysicalSemantics(BaseModel):
    physical_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    selection_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    selected_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    physical_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    default_note_writer: Literal[False]


class PhysicalObservation(BaseModel):
    physical_binding: PhysicalSemantics


class PhysicalPath(BaseModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PhysicalReference(BaseModel):
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    index_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    physical_binding: Literal[True]
    paths: tuple[PhysicalPath, ...]
    observations: dict[str, PhysicalObservation]


def _physical_reference(path: Path, sha256: str) -> PhysicalReference:
    compressed = read_artifact(path)
    if digest(compressed) != sha256:
        raise ValueError("Physical reference compressed SHA differs")
    try:
        source = gzip.decompress(compressed)
    except OSError, EOFError:
        raise ValueError("Physical reference is not a valid gzip artifact") from None
    return PhysicalReference.model_validate_json(source)


def physical_snapshot(data: bytes, *, default_note_writer: bool = False) -> dict[str, Any]:
    reader = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    baseline = reader.extract_parts(data)
    linked = reader.extract_parts_with_doc_notes(data, capture_projection=default_note_writer)
    if linked.legacy != baseline:
        raise ValueError("DOC sidecars changed the legacy physical extraction")
    selected = select_document_parts(linked.legacy, max_part_chars=None)
    if selected != reader.extract_document(data):
        raise ValueError("DOC sidecars changed package selection")
    observed = reader.extract_document_with_provenance(data)
    if observed.document != selected:
        raise ValueError("Default provenance changed selected text")
    without_notes = observed.result.model_dump(mode="json")
    for part in without_notes["selected_parts"]:
        part.pop("doc_notes", None)
    baseline_result = ExtractionResult.model_validate(without_notes)
    if default_note_writer:
        before_word = baseline_result.model_dump(mode="json")
        for part in before_word["selected_parts"]:
            view = part.get("word_view_status")
            if view is not None:
                view.pop("observed_textbox_nodes", None)
                view.pop("observed_inline", None)
        legacy_notes = summarize_doc_notes(
            ExtractionResult.model_validate(before_word), linked, include_projections=False
        )
        parts = tuple(
            part.model_copy(update={"doc_notes": before.doc_notes})
            for part, before in zip(
                baseline_result.selected_parts, legacy_notes.selected_parts, strict=True
            )
        )
        enriched = summarize_doc_note_projections(
            baseline_result.model_copy(update={"selected_parts": parts}), linked
        )
        if observed.result != enriched:
            raise ValueError("Default DOC metadata differs from bounded opt-in capture")
    else:
        if observed.result != baseline_result:
            raise ValueError("Default DOC note writer is enabled; expected writer-OFF mode")
        enriched = summarize_doc_notes(baseline_result, linked)
    without_enrichment = enriched.model_dump(mode="json")
    for part in without_enrichment["selected_parts"]:
        part.pop("doc_notes", None)
    if without_enrichment != without_notes:
        raise ValueError("DOC summaries changed existing extraction fields or omissions")
    summaries = [part.doc_notes for part in enriched.selected_parts if part.doc_notes is not None]
    if (
        sum(len(notes.model_dump_json().encode()) for notes in summaries)
        > MAX_DOC_NOTE_SUMMARY_BYTES
    ):
        raise ValueError("Default DOC summaries exceed the aggregate byte profile")
    if len(enriched.model_dump_json().encode()) > MAX_EXTRACTION_METADATA_BYTES:
        raise ValueError("Default extraction exceeds the whole metadata byte profile")
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
        "omitted_selected_parts": enriched.omitted_selected_parts,
        "default_note_writer": default_note_writer,
    }


_MAX_TRACKED_DOC_CHARS = 1_000_000


def _unit_location(
    pieces: list[tuple[int, int, int, bool]], cp: int
) -> tuple[int, int] | Literal["unsupported_reference", "shared_physical_bytes"]:
    matches = [(index, piece) for index, piece in enumerate(pieces) if piece[0] <= cp < piece[1]]
    if len(matches) != 1:
        return "unsupported_reference"
    index, (first, _, start, compressed) = matches[0]
    width = 1 if compressed else 2
    offset = start + width * (cp - first)
    if any(
        other != index
        and fc < offset + width
        and offset < fc + (end - begin) * (1 if packed else 2)
        for other, (begin, end, fc, packed) in enumerate(pieces)
    ):
        return "shared_physical_bytes"
    return offset, width


def _raw_unit_position(
    word: bytes,
    pieces: list[tuple[int, int, int, bool]],
    spans: list[tuple[int, int]],
    row_ends: frozenset[int],
    raw: str,
    cp: int,
    marker: str,
) -> int | None:
    location = _unit_location(pieces, cp)
    if isinstance(location, str):
        return None
    offset, width = location
    encoding = "cp1252" if width == 1 else "utf-16-le"
    try:
        unit = word[offset : offset + width].decode(encoding)
    except UnicodeDecodeError:
        return None
    altered = word[:offset] + marker.encode(encoding) + word[offset + width :]
    marked = _read_pieces(altered, pieces, spans=spans, row_ends=row_ends)
    if marked.count(marker) != 1 or marked.replace(marker, unit) != raw:
        return None
    return marked.index(marker)


def _project_note_body(
    link: DocNoteLink, locate: Callable[[int], int | None], origins: Sequence[int], baseline: str
) -> dict[str, int | str]:
    start = link.story_start_cp + link.note_start_cp
    last = link.story_start_cp + link.note_end_cp - 1
    first_raw = locate(start)
    last_raw = locate(last)
    if first_raw is None or last_raw is None or first_raw > last_raw:
        return {"status": "unverified_boundary"}
    first_output = bisect_left(origins, first_raw)
    end_output = bisect_right(origins, last_raw)
    return {
        "status": "empty_output" if first_output == end_output else "mapped",
        "output_start": first_output,
        "output_end": end_output,
        "text_sha256": digest(baseline[first_output:end_output].encode()),
    }


def _reference_projection(
    data: bytes,
    links: DocNoteLinks,
    expected: str,
    *,
    track_cleanup: bool = False,
    note_body_projection: bool = False,
) -> list[dict[str, Any]]:
    word, table, extra = _streams(data)
    baseline, pieces, _ = _word_text(word, table, extra)
    if baseline != expected:
        raise ValueError("DOC reference probe changed the baseline text")
    marker = "¦"
    spans = _spans(word) if track_cleanup else []
    row_ends = _row_end_marks(word, table, extra) if track_cleanup else frozenset()
    raw = _read_pieces(word, pieces, spans=spans, row_ends=row_ends) if track_cleanup else ""
    tracked = track_cleanup and len(raw) <= _MAX_TRACKED_DOC_CHARS
    cleaned, origins = _cleanup_positions(raw) if tracked else ("", [])
    if tracked and cleaned != baseline:
        raise ValueError("Tracked DOC cleanup differs from the legacy projection")

    def locate(cp: int) -> int | None:
        return _raw_unit_position(word, pieces, spans, row_ends, raw, cp, marker)

    observations = []
    for link in links.links:
        cp = link.reference_cp
        row: dict[str, Any] = {"kind": link.kind, "reference_cp": cp}
        observations.append(row)
        if note_body_projection:
            if not tracked:
                row["note_body"] = {"status": "input_limit"}
            elif marker in raw:
                row["note_body"] = {"status": "unverified_boundary"}
            else:
                row["note_body"] = _project_note_body(link, locate, origins, baseline)
        if not link.numbering_flag:
            row["status"] = "unsupported_reference"
            continue
        location = _unit_location(pieces, cp)
        if isinstance(location, str):
            row["status"] = location
            continue
        if marker in baseline:
            row["status"] = "marker_already_present"
            continue
        offset, width = location
        encoding = "cp1252" if width == 1 else "utf-16-le"
        if word[offset : offset + width] != "\x02".encode(encoding):
            raise ValueError("DOC reference probe differs from the qualified source unit")
        altered = word[:offset] + marker.encode(encoding) + word[offset + width :]
        projected = word_text(altered, table, extra)
        if projected.count(marker) != 1:
            row["status"] = "marker_not_unique"
        elif projected.replace(marker, "") != baseline:
            row["status"] = "cleanup_changed"
        else:
            row.update(status="anchored", output_offset=projected.index(marker))
        if track_cleanup and not tracked:
            row["cleanup_tracking"] = "input_limit"
        if tracked and row["status"] in ("anchored", "cleanup_changed"):
            marked = _read_pieces(altered, pieces, spans=spans, row_ends=row_ends)
            if marker in raw or marked.count(marker) != 1 or marked.replace(marker, "\x02") != raw:
                if row["status"] == "anchored":
                    raise ValueError("Tracked DOC reference does not match the exact marker")
                continue
            position = bisect_left(origins, marked.index(marker))
            if row["status"] == "anchored":
                if position != row["output_offset"]:
                    raise ValueError("Tracked DOC offset differs from the independent marker")
            else:
                row.update(status="anchored_by_cleanup_map", output_offset=position)
    return observations


def inspect(
    data: bytes,
    *,
    max_notes: int,
    source_characters: bool = False,
    reference_projection: bool = False,
    track_cleanup: bool = False,
    note_body_projection: bool = False,
) -> dict[str, Any]:
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
            **(
                {
                    "reference_projection": _reference_projection(
                        data,
                        text.links,
                        text.legacy_text,
                        track_cleanup=track_cleanup,
                        note_body_projection=note_body_projection,
                    )
                }
                if reference_projection and text.links is not None and text.links.links
                else {}
            ),
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
    reference_projection: bool = False,
    track_cleanup: bool = False,
    note_body_projection: bool = False,
    physical_binding: bool = False,
    reference: Path | None = None,
    reference_sha256: str | None = None,
    default_note_writer: bool = False,
    physical_reference: Path | None = None,
    physical_reference_sha256: str | None = None,
) -> dict[str, Any]:
    corpus = corpus.resolve()
    output = output.resolve()
    if output.is_relative_to(corpus):
        raise ValueError("Replay output must be outside the read-only corpus")
    if max_notes < 0 or max_notes > 256:
        raise ValueError("Replay max_notes must be between 0 and 256")
    if note_body_projection and not track_cleanup:
        raise ValueError("DOC note body projection requires tracked cleanup")
    if track_cleanup and not reference_projection:
        raise ValueError("Tracked DOC cleanup requires reference projection")
    if reference_projection and not source_characters:
        raise ValueError("DOC reference projection requires source-character qualification")
    if physical_binding and not source_characters:
        raise ValueError("Physical DOC binding requires source-character qualification")
    if default_note_writer and (not physical_binding or physical_reference is None):
        raise ValueError("Default DOC writer qualification requires a pinned physical reference")
    physical_prior: PhysicalReference | None = None
    if physical_reference is not None or physical_reference_sha256 is not None:
        if physical_reference is None or physical_reference_sha256 is None or not physical_binding:
            raise ValueError("A pinned physical reference requires physical-binding qualification")
        physical_prior = _physical_reference(physical_reference, physical_reference_sha256)
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
    if physical_prior is not None:
        population = tuple(
            (item.path, item.sha256) for item in manifest.cases if item.extension == ".doc"
        )
        if (
            physical_prior.manifest_sha256 != digest(manifest_bytes)
            or physical_prior.index_sha256 != manifest.index_sha256
            or tuple((item.path, item.sha256) for item in physical_prior.paths) != population
        ):
            raise ValueError("Physical reference and manifest populations differ")
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
                    data,
                    max_notes=max_notes,
                    source_characters=source_characters,
                    reference_projection=reference_projection,
                    track_cleanup=track_cleanup,
                    note_body_projection=note_body_projection,
                )
                if physical_binding:
                    results[item.sha256]["physical_binding"] = physical_snapshot(
                        data, default_note_writer=default_note_writer
                    )
            paths.append({"path": item.path, "sha256": item.sha256})
    if prior is not None:
        if prior.observations.keys() != results.keys():
            raise ValueError("Reference and replay populations differ")
        if any(
            record["legacy_text_sha256"] != prior.observations[sha].legacy_text_sha256
            for sha, record in results.items()
        ):
            raise ValueError("Legacy text differs from the pinned reference")
    if physical_prior is not None:
        if physical_prior.observations.keys() != results.keys():
            raise ValueError("Physical reference and replay populations differ")
        for sha, record in results.items():
            previous = physical_prior.observations[sha].physical_binding.model_dump()
            if any(
                record["physical_binding"][name] != previous[name]
                for name in PHYSICAL_SEMANTIC_FIELDS
            ):
                raise ValueError("Physical semantic hashes differ from the pinned reader baseline")
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
    if reference_projection:
        summary["reference_projection"] = dict(
            Counter(
                item["status"]
                for record in results.values()
                for item in record.get("reference_projection", [])
            )
        )
    if note_body_projection:
        summary["note_body_projection"] = dict(
            Counter(
                item["note_body"]["status"]
                for record in results.values()
                for item in record.get("reference_projection", [])
            )
        )
    report = {
        "version": 1,
        **({"cleanup_tracking": "legacy-text-positions-v1"} if track_cleanup else {}),
        "scope": (
            "Source characters and automatic reference insertion offsets checked where supported"
            if reference_projection
            else "Source characters checked; CFSpec/emitted offsets unknown"
            if source_characters
            else "table-declared CP links only; no text/CFSpec validation"
        ),
        "manifest_sha256": digest(manifest_bytes),
        "index_sha256": manifest.index_sha256,
        "code_version": code_version(),
        "source_fingerprints": source_fingerprints(),
        "physical_binding": physical_binding,
        "reference_sha256": reference_sha256,
        "physical_reference_sha256": physical_reference_sha256,
        "default_note_writer": default_note_writer,
        "physical_reference_scope": (
            "four semantic hashes only; old component/version JSON identity unavailable"
        ),
        "max_notes": max_notes,
        "summary": summary,
        "paths": paths,
        "observations": results,
        "exclusions": [
            "Non-DOC inputs",
            "Nested archive leaves absent from the manifest",
            "CFSpec"
            if note_body_projection
            else "CFSpec and note-body offsets"
            if reference_projection
            else "CFSpec/emitted offset checks"
            if source_characters
            else "Character/CFSpec checks",
            "Independent legal labels",
            *([] if default_note_writer else ["Default runtime capture"]),
            "Full old component/version/extraction JSON identity",
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
    parser.add_argument("--reference-projection", action="store_true")
    parser.add_argument("--track-cleanup", action="store_true")
    parser.add_argument("--note-body-projection", action="store_true")
    parser.add_argument("--physical-binding", action="store_true")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--reference-sha256")
    parser.add_argument("--default-note-writer", action="store_true")
    parser.add_argument("--physical-reference", type=Path)
    parser.add_argument("--physical-reference-sha256")
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.corpus,
                args.manifest,
                args.output,
                max_notes=args.max_notes,
                source_characters=args.source_characters,
                reference_projection=args.reference_projection,
                track_cleanup=args.track_cleanup,
                note_body_projection=args.note_body_projection,
                physical_binding=args.physical_binding,
                reference=args.reference,
                reference_sha256=args.reference_sha256,
                default_note_writer=args.default_note_writer,
                physical_reference=args.physical_reference,
                physical_reference_sha256=args.physical_reference_sha256,
            )
        )
    )


if __name__ == "__main__":
    main()

"""Audit retained ODT notes and table coordinates in original roots and observed leaves."""

import argparse
import io
import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.document_candidates import select_document_parts
from lexinform.extraction_records import summarize_extraction
from lexinform.models.extractions import ExtractionResult
from lexinform.models.odt_notes import OdtNoteCall
from lexinform.models.odt_tables import OdtTableMerges
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_manifests import RawReplayManifest
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints
from tools.replay_odt_blocks import OFFICE, TEXT, verify_manifest, verify_note_origins


def reader() -> DocumentTextExtractor:
    return DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())


def member(data: bytes, names: tuple[str, ...]) -> bytes:
    for name in names:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entry = archive.getinfo(name)
            if entry.file_size > 32_000_000:
                raise ValueError("Observed ODT member exceeds the existing physical byte limit")
            data = archive.read(entry)
    return data


def xml_inventory(
    body: ET.Element,
) -> tuple[dict[tuple[int, ...], ET.Element], dict[str, list[tuple[int, ...]]]]:
    nodes: dict[tuple[int, ...], ET.Element] = {}
    definitions: dict[str, list[tuple[int, ...]]] = {}

    def visit(node: ET.Element, path: tuple[int, ...]) -> None:
        nodes[path] = node
        if node.tag == TEXT + "note":
            identity = node.get(TEXT + "id")
            if identity is not None:
                definitions.setdefault(identity, []).append(path)
        for index, child in enumerate(node):
            visit(child, (*path, index))

    visit(body, ())
    return nodes, definitions


def verify_retained_tables(
    stored: OdtTableMerges | None, layouts: list[dict[str, Any]]
) -> dict[str, int | str]:
    verified = {row["table_path"]: row["merges"] for row in layouts if row["status"] == "verified"}
    unresolved = sum(row["status"] == "unresolved" for row in layouts)
    counts: dict[str, int | str] = {
        "verified_xml_tables": len(verified),
        "unresolved_xml_tables": unresolved,
        "retained_tables": 0,
        "retained_merges": 0,
        "status": "not_retained",
    }
    if stored is None:
        return counts
    if (stored.verified_tables, stored.unresolved_tables) != (len(verified), unresolved):
        raise ValueError("Stored ODT table counts differ from the pinned XML")
    for table in stored.tables:
        expected = verified.get(table.path)
        actual = [
            {
                "anchor": merge.anchor,
                "covered": list(merge.covered),
                "logical_anchor": merge.position,
                "logical_covered": sorted(merge.positions() - {merge.position}),
            }
            for merge in table.merges
        ]
        if actual != expected:
            raise ValueError("Stored ODT merge coordinates differ from the pinned XML")
    counts.update(
        status="verified",
        retained_tables=len(stored.tables),
        retained_merges=sum(len(table.merges) for table in stored.tables),
    )
    return counts


def audit_context(data: bytes, *, context: str) -> tuple[dict[str, Any], list[tuple[str, bytes]]]:
    native = reader()
    linked = native.extract_parts_with_odt_notes(data)
    physical = native.extract_parts(data)
    if linked.legacy != physical:
        raise ValueError("ODT sidecar capture changes actual native physical results")
    physical_indices = [part.part_index for part in physical.odt_blocks]
    captured_indices = [part.part_index for part in linked.odt_notes]
    if (
        len(set(physical_indices)) != len(physical_indices)
        or len(set(captured_indices)) != len(captured_indices)
        or set(physical_indices) != set(captured_indices)
    ):
        raise ValueError("Every observed physical ODT part requires one distinct note companion")
    selected = select_document_parts(physical, max_part_chars=None)
    plain = native.extract_document(data)
    observed = native.extract_document_with_provenance(data)
    if selected != plain or plain != observed.document:
        raise ValueError("ODT default provenance changes actual native selection or text")
    result = ExtractionResult.model_validate_json(observed.result.model_dump_json())
    baseline = summarize_extraction(
        physical, selected, components=result.components, options=result.options
    )
    if (
        result.physical_manifest_sha256,
        result.output_text_sha256,
        result.selected_part_count,
        result.omitted_selected_parts,
    ) != (
        baseline.physical_manifest_sha256,
        baseline.output_text_sha256,
        baseline.selected_part_count,
        baseline.omitted_selected_parts,
    ):
        raise ValueError("ODT diagnostics changed physical/text identity or existing omissions")
    bodies = {item.part_index: item.extraction for item in physical.odt_blocks}
    summaries = {item.physical_index: item for item in result.selected_parts}
    leaves = []
    payloads = []
    for item in linked.odt_notes:
        index, value = item.part_index, item.extraction
        part = physical.parts[index]
        raw = member(data, part.members)
        if digest(raw) != part.sha256 or value.input_sha256 != part.sha256:
            raise ValueError("ODT leaf bytes differ from the actual observed physical slot")
        payloads.append((part.sha256, raw))
        body = bodies[index]
        coordinates = verify_manifest(raw, body)
        verification = verify_note_origins(raw, body, value)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            xml = archive.read("content.xml")
        xml_body = ET.fromstring(xml).find(OFFICE + "body/" + OFFICE + "text")
        if xml_body is None:
            raise ValueError("The pinned readable ODT cohort unexpectedly has no text body")
        nodes, definitions = xml_inventory(xml_body)
        associations: Counter[str] = Counter()
        for call in value.calls:
            node = nodes[call.path]
            if call.kind == "embedded":
                if (
                    call.association != "embedded"
                    or call.target_path != call.path
                    or node.tag != TEXT + "note"
                ):
                    raise ValueError("Embedded ODT call does not identify its own XML note")
            elif call.target_path is not None:
                expected = definitions.get(node.get(TEXT + "ref-name", ""), [])
                if expected != [call.target_path]:
                    raise ValueError("Resolved ODT reference lacks a unique independent XML target")
            associations[call.kind + "/" + call.association] += 1
        summary = summaries.get(index)
        prefix = summary.odt_note_prefix if summary is not None else None
        status = summary.odt_note_status if summary is not None else None
        source_events = sorted([*value.entries, *value.calls], key=lambda event: event.order)
        stored_events = (
            sorted([*prefix.origins.entries, *prefix.origins.calls], key=lambda event: event.order)
            if prefix is not None
            else []
        )
        if prefix is not None:
            if prefix.physical_index != index or (
                prefix.origins.input_sha256,
                prefix.origins.xml_sha256,
                prefix.origins.body_manifest_sha256,
            ) != (part.sha256, value.xml_sha256, value.body_manifest_sha256):
                raise ValueError("Stored ODT prefix has a different physical/XML/body identity")
            if len(stored_events) > len(source_events):
                raise ValueError("Stored ODT events exceed the observed capture")
            for stored, source in zip(stored_events, source_events, strict=False):
                if stored.model_dump(exclude={"association", "target_path"}) != source.model_dump(
                    exclude={"association", "target_path"}
                ):
                    raise ValueError("Stored ODT event prefix differs from the observed capture")
                if stored != source and not (
                    isinstance(stored, OdtNoteCall)
                    and stored.association == "unverified"
                    and stored.target_path is None
                    and len(prefix.origins.entries) < len(value.entries)
                ):
                    raise ValueError("Stored ODT association changed without omitted definitions")
            verify_note_origins(raw, body, prefix.origins)
            block_bounds = {block.path: block for block in body.blocks}
            for witness in prefix.blocks:
                actual = block_bounds[witness.path]
                if (witness.kind, witness.chars) != (actual.kind, len(actual.text)):
                    raise ValueError(
                        "Stored ODT block witness differs from the observed source block"
                    )
        if status is not None and (
            status.input_sha256,
            status.body_manifest_sha256,
            status.retained_manifest_sha256,
            status.observed_notes,
            status.observed_calls,
        ) != (
            part.sha256,
            value.body_manifest_sha256,
            value.retained_manifest_sha256,
            value.note_count,
            value.call_count,
        ):
            raise ValueError("Stored ODT status differs from the independent actual leaf companion")
        leaves.append(
            {
                "part_index": index,
                "input_sha256": part.sha256,
                "member_chain_sha256": digest(json.dumps(part.members).encode()),
                "selected": summary is not None,
                "note_count": value.note_count,
                "call_count": value.call_count,
                "raw_xml_notes": sum(node.tag == TEXT + "note" for node in nodes.values()),
                "raw_xml_separate_refs": sum(
                    node.tag == TEXT + "note-ref" for node in nodes.values()
                ),
                "captured_separate_refs": sum(call.kind == "reference" for call in value.calls),
                "associations": dict(associations),
                "verified_xml": verification,
                "raw_note_classes": dict(
                    Counter(
                        node.get(TEXT + "note-class")
                        if node.get(TEXT + "note-class") in ("footnote", "endnote")
                        else "other"
                        for node in nodes.values()
                        if node.tag == TEXT + "note"
                    )
                ),
                "observed_emitted_spans": sum(event.emitted is not None for event in source_events),
                "retained_prefix": prefix is not None,
                "stored_events": len(stored_events),
                "captured_events": len(source_events),
                "status_contents": status.contents if status is not None else None,
                "text_view": coordinates["text_view"],
                "tracked_changes": coordinates["tracked_changes"],
                "table_repetitions": coordinates["table_repetitions"],
                "table_merges": verify_retained_tables(
                    summary.odt_table_merges if summary is not None else None,
                    coordinates["merged_tables"],
                ),
                "body_manifest_sha256": body.manifest_sha256,
            }
        )
    prefix_bytes = sum(
        len(
            json.dumps(
                part.odt_note_prefix.model_dump(mode="json"),
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii")
        )
        for part in result.selected_parts
        if part.odt_note_prefix is not None
    )
    if prefix_bytes > 4096 or len(result.model_dump_json().encode()) > 16384:
        raise ValueError("ODT default prefix exceeds its aggregate or whole byte limit")
    return {
        "context": context,
        "input_sha256": digest(data),
        "physical_parts": len(physical.parts),
        "selected_parts": result.selected_part_count,
        "selected_summary_omissions": result.omitted_selected_parts,
        "selection_sha256": digest(selected.selection.model_dump_json().encode()),
        "selected_text_sha256": digest(selected.text.encode()),
        "physical_manifest_sha256": result.physical_manifest_sha256,
        "odt_prefix_bytes": prefix_bytes,
        "whole_summary_bytes": len(result.model_dump_json().encode()),
        "odt_leaves": leaves,
    }, payloads


def run(corpus: Path, manifest_path: Path, output: Path) -> dict[str, Any]:
    corpus = corpus.resolve(strict=True)
    target = output.resolve()
    if output.is_symlink() or target.exists() or target.is_relative_to(corpus):
        raise ValueError("Output must be a new directory outside the read-only corpus")
    raw_manifest = read_artifact(manifest_path)
    manifest = RawReplayManifest.model_validate_json(raw_manifest)
    paths = [case.path for case in manifest.cases]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate original download-root paths")
    before = source_fingerprints()
    reads = CorpusReads(corpus)
    roots: list[dict[str, Any]] = []
    standalone: list[dict[str, Any]] = []
    payloads: dict[str, bytes] = {}
    with reads.guard():
        if digest((corpus / "INDEX.json").read_bytes()) != manifest.index_sha256:
            raise ValueError("Corpus INDEX differs from the original cohort")
        for case in manifest.cases:
            if Path(case.path).is_absolute():
                raise ValueError("Original download-root paths must be corpus-relative")
            path = corpus / case.path
            reads.require(path)
            data = path.read_bytes()
            if digest(data) != case.sha256:
                raise ValueError("Original download root differs from the pinned raw SHA")
            record, reached = audit_context(data, context=case.path)
            roots.append(record)
            payloads.update(reached)
        for identity, data in sorted(payloads.items()):
            record, _ = audit_context(data, context="standalone-observed-leaf")
            if record["input_sha256"] != identity:
                raise ValueError("Standalone leaf qualification substituted different bytes")
            standalone.append(record)
    if not any(record["odt_leaves"] for record in roots):
        raise ValueError("The cohort contains no observed physical ODT parts")
    changed = reads.changed_inputs()
    if changed or before != source_fingerprints():
        raise ValueError("Pinned corpus or running implementation changed during the bounded audit")
    original_leaves = [leaf for record in roots for leaf in record["odt_leaves"]]
    leaf_runs = [leaf for record in standalone for leaf in record["odt_leaves"]]
    summary = {
        "original_download_roots": len(roots),
        "original_odt_leaf_occurrences": len(original_leaves),
        "unique_standalone_leaf_runs": len(standalone),
        "original_selected_odt_leaves": sum(leaf["selected"] for leaf in original_leaves),
        "original_selected_note_positive": sum(
            leaf["selected"] and leaf["note_count"] > 0 for leaf in original_leaves
        ),
        "standalone_selected_note_positive": sum(
            leaf["selected"] and leaf["note_count"] > 0 for leaf in leaf_runs
        ),
        "original_raw_xml_notes": sum(leaf["raw_xml_notes"] for leaf in original_leaves),
        "original_raw_xml_separate_refs": sum(
            leaf["raw_xml_separate_refs"] for leaf in original_leaves
        ),
        "original_verified_xml_note_paths": sum(
            leaf["verified_xml"]["verified_note_paths"] for leaf in original_leaves
        ),
        "original_verified_xml_call_paths": sum(
            leaf["verified_xml"]["verified_call_paths"] for leaf in original_leaves
        ),
        "original_independent_plain_spans": sum(
            leaf["verified_xml"]["verified_plain_spans"] for leaf in original_leaves
        ),
        "original_independent_inline_space_spans": sum(
            leaf["verified_xml"].get("verified_inline_space_spans", 0) for leaf in original_leaves
        ),
        "standalone_independent_inline_space_spans": sum(
            leaf["verified_xml"].get("verified_inline_space_spans", 0) for leaf in leaf_runs
        ),
        "standalone_retained_note_positive": sum(
            leaf["selected"] and leaf["note_count"] > 0 and leaf["retained_prefix"]
            for leaf in leaf_runs
        ),
        "original_retained_table_merges": sum(
            leaf["table_merges"]["retained_merges"] for leaf in original_leaves
        ),
        "standalone_retained_table_merges": sum(
            leaf["table_merges"]["retained_merges"] for leaf in leaf_runs
        ),
        "changed_inputs": changed,
        "verified_input_paths": len(reads.paths),
        "source_fingerprints_unchanged": True,
    }
    report = {
        "scope": (
            "Explicit original download roots and their observed standalone ODT bytes; "
            "no discovery or legal labels"
        ),
        "code_version": code_version(),
        "driver_sha256": digest(read_artifact(Path(__file__))),
        "input_sha256_before": reads.initial_sha256,
        "source_fingerprints_sha256": digest(json.dumps(before, sort_keys=True).encode()),
        "manifest_sha256": digest(raw_manifest),
        "index_sha256": manifest.index_sha256,
        "summary": summary,
        "original_roots": roots,
        "standalone_leaf_contexts": standalone,
        "limitations": [
            "Standalone selection is not original download-root selection",
            "Independent plaintext spans counted only where the existing oracle supports them",
            "Separate references absent remain unobserved, not accepted",
            "No raw/source body retained in this report; no download or provider call",
        ],
    }
    target.mkdir(parents=True, exist_ok=False)
    write_json(target / "report.json", report)
    write_json(target / "summary.json", summary)
    write_json(target / "source-pins.json", before)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.corpus, args.manifest, args.output)))


if __name__ == "__main__":
    main()

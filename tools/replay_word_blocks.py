"""Replay OOXML Word bodies with physical XML paths and unchanged legacy text."""

import argparse
import io
import json
import logging
import zipfile
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree as ET

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models import ExtractedParts, WordDetailedExtraction, WordTextExtraction
from tools.corpus_artifacts import digest, observation_changes, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints


def word_envelope(data: bytes, reader: DocxTextExtractor) -> ExtractedParts:
    extracted = DocumentTextExtractor(
        PypdfTextExtractor(), reader, DocTextExtractor()
    ).extract_parts(data, include_pdf_pages=False)
    if not extracted.word_blocks:
        if any(part.text for part in extracted.parts):
            raise RuntimeError("Word parts have text without their manifest")
        reader.extract(data)
        raise RuntimeError("Word parts lost a readable manifest")
    if len(extracted.parts) != 1 or len(extracted.word_blocks) != 1:
        raise RuntimeError("A single Word input must retain one physical part")
    linked = extracted.word_blocks[0]
    if linked.part_index != 0 or extracted.rejected:
        raise RuntimeError("Word manifest has an invalid physical link")
    return extracted


def physical_word(data: bytes, reader: DocxTextExtractor) -> WordTextExtraction:
    return word_envelope(data, reader).word_blocks[0].extraction


def physical_notes(data: bytes, reader: DocxTextExtractor) -> WordDetailedExtraction:
    result = word_envelope(data, reader)
    if len(result.word_notes) != 1 or result.word_notes[0].part_index != 0:
        raise RuntimeError("A Word note manifest must bind to its single physical part")
    return WordDetailedExtraction(
        body=result.word_blocks[0].extraction, notes=result.word_notes[0].extraction
    )


def snapshot(
    data: bytes, *, adapter: bool, physical_parts: bool = False, with_notes: bool = False
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    reader = DocxTextExtractor()
    result = None
    detailed = None
    try:
        if adapter:
            if with_notes:
                detailed = (
                    physical_notes(data, reader)
                    if physical_parts
                    else reader.extract_with_notes(data)
                )
                result = detailed.body
            else:
                result = (
                    physical_word(data, reader) if physical_parts else reader.extract_blocks(data)
                )
            text = result.legacy_text
        else:
            text = reader.extract(data)
        core = {
            "input_sha256": digest(data),
            "text_sha256": digest(text.encode()),
            "error_type": None,
        }
    except Exception as exc:
        return {
            "input_sha256": digest(data),
            "text_sha256": None,
            "error_type": type(exc).__name__,
        }, None
    if result is None:
        return core, None
    restored = type(result).model_validate_json(result.model_dump_json())
    if restored.manifest_sha256 != result.manifest_sha256 or result.input_sha256 != digest(data):
        raise ValueError("Word manifest identity does not survive a round trip")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.getinfo("word/document.xml").file_size > 32_000_000:
            raise ValueError("Word XML exceeds the reader's bound")
        xml = archive.read("word/document.xml")
    if digest(xml) != result.xml_sha256:
        raise ValueError("Word manifest refers to different XML bytes")
    if result.body_state == "read":
        root = ET.fromstring(xml)
        namespace = root.tag.rsplit("}", 1)[0] + "}"
        if root.find(namespace + "body") is None:
            raise ValueError("A read Word body must exist")
        for block in result.blocks:
            node = root
            for index in block.path:
                node = node[index]
            tag = {"paragraph": "p", "table_row": "tr", "table_cell": "tc"}[block.kind]
            if node.tag != namespace + tag:
                raise ValueError("Word block path does not address its declared XML element")
    physical = {
        "manifest_sha256": result.manifest_sha256,
        "xml_sha256": result.xml_sha256,
        "body_state": result.body_state,
        "legacy_fragments": len(result.legacy_blocks),
        "block_counts": {
            kind: sum(block.kind == kind for block in result.blocks)
            for kind in ("paragraph", "table_row", "table_cell")
        },
        "verified_coordinates": len(result.blocks),
    }
    if detailed is not None:
        notes = detailed.notes
        if (
            notes.input_sha256 != result.input_sha256
            or notes.body_manifest_sha256 != result.manifest_sha256
        ):
            raise ValueError("Word notes refer to a different body extraction")
        restored_notes = type(notes).model_validate_json(notes.model_dump_json())
        if restored_notes.manifest_sha256 != notes.manifest_sha256:
            raise ValueError("Word note identity does not survive a round trip")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            roots: dict[str, ET.Element] = {}
            for part in notes.parts:
                if archive.getinfo(part.name).file_size > 32_000_000:
                    raise ValueError("Word note XML exceeds the reader's bound")
                content = archive.read(part.name)
                if digest(content) != part.sha256:
                    raise ValueError("Word note part refers to different XML bytes")
                if part.state == "read":
                    roots[part.name] = ET.fromstring(content)
        for note in notes.notes:
            node = roots[note.xml_part]
            namespace = node.tag.rsplit("}", 1)[0] + "}"
            for index in note.path:
                node = node[index]
            tag = "footnote" if note.xml_part == "word/footnotes.xml" else "endnote"
            if node.tag != namespace + tag or node.get(namespace + "id") != note.identity:
                raise ValueError("Word note path does not address its declared XML element")
        if notes.calls:
            root = ET.fromstring(xml)
            namespace = root.tag.rsplit("}", 1)[0] + "}"
            for call in notes.calls:
                node = root
                for index in call.path:
                    node = node[index]
                if (
                    node.tag != namespace + call.kind + "Reference"
                    or node.get(namespace + "id", "") != call.identity
                ):
                    raise ValueError("Word note call does not address its declared XML element")
        physical["notes"] = {
            "manifest_sha256": notes.manifest_sha256,
            "parts": len(notes.parts),
            "verified_note_coordinates": len(notes.notes),
            "verified_call_coordinates": len(notes.calls),
            "states": {
                state: sum(call.state == state for call in notes.calls)
                for state in ("expanded", "empty", "missing", "unverified")
            },
            "losses": notes.losses,
        }
    return core, physical


def run(
    corpus: Path,
    catalog: Path,
    catalog_sha256: str,
    output: Path,
    *,
    mode: Literal["library", "adapter", "parts", "notes", "notes-parts"],
    reference: Path | None = None,
    reference_sha256: str | None = None,
) -> int:
    root, target = corpus.resolve(strict=True), output.resolve()
    if output.is_symlink() or target.exists() or target.is_relative_to(root):
        raise ValueError("Output must be a new file outside the corpus")
    raw = read_artifact(catalog)
    inventory = json.loads(raw)
    if (
        digest(raw) != catalog_sha256
        or inventory.get("status") != "recorded"
        or inventory.get("suite") != "external"
    ):
        raise ValueError("A pinned complete external catalog is required")
    inputs = {
        "documents/files/" + Path(key).name: row["actual"]["sha256"]
        for key, row in inventory["observations"].items()
        if key.startswith("word/") and Path(key).suffix in (".docx", ".docm")
    }
    if not inputs:
        raise ValueError("Word population must not be empty")
    previous = None
    if mode != "library":
        if reference is None or reference_sha256 is None:
            raise ValueError("Adapter replay needs a pinned library baseline")
        raw_reference = read_artifact(reference)
        if digest(raw_reference) != reference_sha256:
            raise ValueError("Reference SHA mismatch")
        previous = json.loads(raw_reference)
        if (
            previous.get("status") != "recorded"
            or previous.get("suite") != "word-blocks-v1"
            or previous.get("catalog_sha256") != catalog_sha256
        ):
            raise ValueError("Reference must contain the same complete Word population")
    elif reference is not None or reference_sha256 is not None:
        raise ValueError("Library baseline does not accept a reference")
    before = source_fingerprints()
    result: dict[str, Any] = {
        "suite": "word-blocks-v1",
        "entrypoint": "physical_word_notes"
        if mode == "notes-parts"
        else "word_notes"
        if mode == "notes"
        else "physical_parts"
        if mode == "parts"
        else "word_leaf",
        "code_sha": code_version(),
        "source_sha256": before,
        "catalog_sha256": catalog_sha256,
        "observations": {},
        "physical": {},
        "errors": {},
        "changes": [],
        "oracle": "machine_library_regression",
        "independent_legal_oracle": False,
        "exclusions": [
            "DOCX/DOCM body only; standalone Word XML, ODT and legacy DOC are excluded.",
            "No visual pagination, standalone Word XML, full layout or completeness claim.",
            "Unsupported leaf readers must retain their observed exception; no fabricated blocks.",
        ],
    }
    reads = CorpusReads(root)
    with reads.guard():
        for name, sha in sorted(inputs.items()):
            try:
                path = root / name
                if not path.resolve().is_relative_to(root):
                    raise ValueError("Input escapes corpus")
                data = path.read_bytes()
                if digest(data) != sha:
                    raise ValueError("Raw input SHA mismatch")
                core, physical = snapshot(
                    data,
                    adapter=mode != "library",
                    physical_parts=mode in ("parts", "notes-parts"),
                    with_notes=mode in ("notes", "notes-parts"),
                )
                result["observations"][name] = core
                if physical is not None:
                    result["physical"][name] = physical
            except Exception as exc:
                result["errors"][name] = f"{type(exc).__name__}: {exc}"
    result["read_inputs"] = dict(sorted(reads.initial_sha256.items()))
    result["changed_inputs"] = reads.changed_inputs()
    result["changed_code"] = before != source_fingerprints()
    if previous is not None:
        result["reference_sha256"] = reference_sha256
        result["changes"] = observation_changes(previous["observations"], result["observations"])
    failed = bool(
        result["errors"] or result["changes"] or result["changed_code"] or result["changed_inputs"]
    )
    result["status"] = "failed" if failed else "passed" if previous is not None else "recorded"
    write_json(target, result)
    return int(failed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--catalog-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--mode", choices=("library", "adapter", "parts", "notes", "notes-parts"), required=True
    )
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--reference-sha256")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    raise SystemExit(
        run(
            args.corpus,
            args.catalog,
            args.catalog_sha256,
            args.output,
            mode=args.mode,
            reference=args.reference,
            reference_sha256=args.reference_sha256,
        )
    )


if __name__ == "__main__":
    main()

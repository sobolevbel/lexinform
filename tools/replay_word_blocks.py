"""Replay OOXML Word bodies with physical XML paths and unchanged legacy text."""

import argparse
import io
import json
import logging
import zipfile
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree as ET

from lexinform.adapters.document_text import DocxTextExtractor
from tools.corpus_check import code_version, source_fingerprints
from tools.corpus_external import CorpusReads
from tools.replay_input_coverage import digest, read_artifact


def snapshot(data: bytes, *, adapter: bool) -> tuple[dict[str, Any], dict[str, Any] | None]:
    reader = DocxTextExtractor()
    result = None
    try:
        if adapter:
            result = reader.extract_blocks(data)
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
    return core, physical


def run(
    corpus: Path,
    catalog: Path,
    catalog_sha256: str,
    output: Path,
    *,
    mode: Literal["library", "adapter"],
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
    if mode == "adapter":
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
            "No visual pagination, note-part coordinates, full layout or completeness claim.",
            "Unsupported leaf readers must retain their observed exception; no fabricated blocks.",
        ],
    }
    reads = CorpusReads(root)
    with reads.guard():
        for name, sha in sorted(inputs.items()):
            try:
                path = (root / name).resolve()
                if not path.is_relative_to(root):
                    raise ValueError("Input escapes corpus")
                data = path.read_bytes()
                if digest(data) != sha:
                    raise ValueError("Raw input SHA mismatch")
                core, physical = snapshot(data, adapter=mode == "adapter")
                result["observations"][name] = core
                if physical is not None:
                    result["physical"][name] = physical
            except Exception as exc:
                result["errors"][name] = f"{type(exc).__name__}: {exc}"
    result["changed_inputs"] = [
        name
        for name, sha in reads.initial_sha256.items()
        if not (root / name).is_file() or digest((root / name).read_bytes()) != sha
    ]
    result["changed_code"] = before != source_fingerprints()
    if previous is not None:
        result["reference_sha256"] = reference_sha256
        result["changes"] = [
            name
            for name in sorted(previous["observations"].keys() | result["observations"].keys())
            if previous["observations"].get(name) != result["observations"].get(name)
        ]
    failed = bool(
        result["errors"] or result["changes"] or result["changed_code"] or result["changed_inputs"]
    )
    result["status"] = "failed" if failed else "passed" if previous is not None else "recorded"
    with target.open("x") as stream:
        json.dump(result, stream, sort_keys=True, ensure_ascii=False, indent=2)
        stream.write("\n")
    return int(failed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--catalog-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("library", "adapter"), required=True)
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

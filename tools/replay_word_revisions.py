"""Probe the legacy extraction of text in OOXML revision wrappers and fields."""

import argparse
import io
import json
import zipfile
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree as ET

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints

_BODY = "word/document.xml"
_WORD_NAMESPACES = (
    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}",
    "{http://purl.oclc.org/ooxml/wordprocessingml/main}",
)
_TEXT_BY_WRAPPER = {
    "ins": ("t",),
    "del": ("delText", "t"),
    "moveFrom": ("delText", "t"),
    "moveTo": ("t",),
}
_MAX_ARCHIVE_BYTES = 32_000_000
_MAX_BODY_BYTES = 8_000_000
_MAX_EXPANDED_BYTES = 64_000_000
_MAX_ENTRIES = 4096


Profile = Literal["revisions", "fields"]


def _probe(
    data: bytes, adapter: DocumentTextExtractor, profile: Profile = "revisions"
) -> dict[str, object]:
    if len(data) > _MAX_ARCHIVE_BYTES:
        raise ValueError("Word archive exceeds the projection replay bound")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if (
            len(entries) > _MAX_ENTRIES
            or len({entry.filename for entry in entries}) != len(entries)
            or sum(entry.file_size for entry in entries) > _MAX_EXPANDED_BYTES
        ):
            raise ValueError("Expected a bounded Word archive with unique entry names")
        entry = archive.getinfo(_BODY)
        if entry.file_size > _MAX_BODY_BYTES:
            raise ValueError("Word body exceeds the projection replay bound")
        raw = archive.read(entry)
        xml = raw.decode("utf-8-sig")
        if "\x00" in xml or "<!DOCTYPE" in xml or "<!ENTITY" in xml:
            raise ValueError("Unsupported XML encoding or declaration in Word body")
        root = ET.fromstring(raw)
        namespace = next((ns for ns in _WORD_NAMESPACES if root.tag == ns + "document"), None)
        if namespace is None or (body := root.find(namespace + "body")) is None:
            raise ValueError("Expected an OOXML document body")
        package = {entry.filename: archive.read(entry) for entry in entries}

    original = adapter.extract_document_with_provenance(data).document.text
    report: dict[str, object] = {
        "input_sha256": digest(data),
        "body_xml_sha256": digest(raw),
        "text_sha256": digest(original.encode()),
    }
    report["noop_text_identical"] = (
        adapter.extract_document_with_provenance(_pack(package, root)).document.text == original
    )
    if not report["noop_text_identical"]:
        return report

    groups = (
        _revision_text_groups(body, namespace)
        if profile == "revisions"
        else _field_text_groups(body, namespace)
    )
    if groups is None:
        report["field_sequence"] = "unbalanced"
        return report

    marker_prefix = "ZLEXREVISION" if profile == "revisions" else "ZLEXFIELD"
    markers: dict[str, list[str]] = {}
    changed: set[ET.Element] = set()
    for group, nodes in groups.items():
        markers[group] = []
        for index, node in enumerate(nodes):
            if node in changed:
                raise ValueError("Probe text belongs to multiple observation classes")
            label = group.upper().replace("/", "").replace("_", "")
            marker = f"{marker_prefix}{label}{index:06d}ZZ"
            if marker in original:
                raise ValueError("Probe marker already appears in the legacy text")
            node.text = marker
            markers[group].append(marker)
            changed.add(node)

    mutated = adapter.extract_document_with_provenance(_pack(package, root)).document.text
    report["observations"] = {
        key: {"mutated": len(values), "emitted": sum(marker in mutated for marker in values)}
        for key, values in markers.items()
    }
    return report


def _revision_text_groups(body: ET.Element, namespace: str) -> dict[str, list[ET.Element]]:
    groups = {}
    for wrapper, kinds in _TEXT_BY_WRAPPER.items():
        for kind in kinds:
            nodes = {
                node
                for parent in body.iter(namespace + wrapper)
                for node in parent.iter(namespace + kind)
                if node.text
            }
            groups[f"{wrapper}/{kind}"] = [node for node in body.iter() if node in nodes]
    return groups


def _field_text_groups(body: ET.Element, namespace: str) -> dict[str, list[ET.Element]] | None:
    groups: dict[str, list[ET.Element]] = {
        "instruction": [],
        "simple_body_text": [],
        "before_separator": [],
        "after_separator": [],
    }
    simple_text = {
        node for field in body.iter(namespace + "fldSimple") for node in field.iter(namespace + "t")
    }
    stack: list[str] = []
    for node in body.iter():
        if node.tag == namespace + "fldChar":
            marker = node.get(namespace + "fldCharType")
            if marker == "begin":
                stack.append("before_separator")
            elif marker == "separate" and stack and stack[-1] == "before_separator":
                stack[-1] = "after_separator"
            elif marker == "end" and stack:
                stack.pop()
            else:
                return None
        elif node.text:
            if node.tag == namespace + "instrText":
                groups["instruction"].append(node)
            elif node.tag == namespace + "t":
                if node in simple_text:
                    groups["simple_body_text"].append(node)
                elif stack:
                    groups[stack[-1]].append(node)
    return None if stack else groups


def _pack(package: dict[str, bytes], root: ET.Element) -> bytes:
    buffer = io.BytesIO()
    body = ET.tostring(root, encoding="utf-8")
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in package.items():
            archive.writestr(name, body if name == _BODY else data)
    return buffer.getvalue()


def run(
    corpus: Path,
    catalog: Path,
    catalog_sha256: str,
    cases: tuple[str, ...],
    output: Path,
    *,
    profile: Profile = "revisions",
) -> None:
    root, target = corpus.resolve(strict=True), output.resolve()
    if output.is_symlink() or target.exists() or target.is_relative_to(root):
        raise ValueError("Output must be a new file outside the corpus")
    if not 1 <= len(cases) <= 64 or len(set(cases)) != len(cases):
        raise ValueError("Expected up to 64 unique Word basenames")
    raw = read_artifact(catalog)
    inventory = json.loads(raw)
    if (
        digest(raw) != catalog_sha256
        or inventory.get("status") != "recorded"
        or inventory.get("suite") != "external"
    ):
        raise ValueError("A pinned external extraction catalog is required")
    before = source_fingerprints()
    reads = CorpusReads(root)
    adapter = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    rows = {}
    with reads.guard():
        for name in sorted(cases):
            if name != Path(name).name or Path(name).suffix not in (".docx", ".docm"):
                raise ValueError("Expected an OOXML basename from the pinned catalog")
            pinned = inventory["observations"].get(f"word/{name}")
            if pinned is None:
                raise ValueError("Word input is outside the pinned catalog")
            relative = f"documents/files/{name}"
            with (root / relative).open("rb") as stream:
                data = stream.read(_MAX_ARCHIVE_BYTES + 1)
            if digest(data) != pinned["actual"]["sha256"]:
                raise ValueError("Raw Word input SHA mismatch")
            rows[relative] = _probe(data, adapter, profile)
    if reads.changed_inputs() or before != source_fingerprints():
        raise ValueError("Word revision replay input or code changed during the run")
    write_json(
        target,
        {
            "suite": "word-revision-projection-v1"
            if profile == "revisions"
            else "word-field-projection-v1",
            "status": "recorded",
            "scope": "Synthetic text replacement in pinned OOXML body revision wrappers"
            if profile == "revisions"
            else "Synthetic text replacement in physically ordered OOXML body fields",
            "completeness": "not_asserted",
            "catalog_sha256": catalog_sha256,
            "code_sha": code_version(),
            "source_sha256": before,
            "observations": rows,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--catalog-sha256", required=True)
    parser.add_argument("--case", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=("revisions", "fields"), default="revisions")
    args = parser.parse_args()
    run(
        args.corpus,
        args.catalog,
        args.catalog_sha256,
        tuple(args.case),
        args.output,
        profile=args.profile,
    )


if __name__ == "__main__":
    main()

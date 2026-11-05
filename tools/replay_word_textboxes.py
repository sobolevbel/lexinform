"""Compare bounded OOXML textbox inventory with the native legacy walk, without retaining text."""

import argparse
import io
import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Literal, TypedDict
from unittest.mock import patch
from xml.etree import ElementTree as ET

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models.word_structure import WordStructureView
from lexinform.word_structure import WordStructureCollector
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints

_BODY = "word/document.xml"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_WORD_NAMESPACES = (
    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}",
    "{http://purl.oclc.org/ooxml/wordprocessingml/main}",
)
_MAX_ARCHIVE_BYTES = 32_000_000
_MAX_BODY_BYTES = 8_000_000
_MAX_CASES = 64

Branch = Literal["ordinary", "choice", "fallback"]


class TextboxObservation(TypedDict):
    input_sha256: str
    body_xml_sha256: str
    legacy_text_sha256: str
    physical_manifest_sha256: str
    raw_textboxes: dict[Branch, int]
    visited_textboxes: dict[WordStructureView, int]


def _read_body(data: bytes) -> tuple[str, ET.Element, str]:
    if len(data) > _MAX_ARCHIVE_BYTES:
        raise ValueError("Word archive exceeds the textbox replay bound")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = [item for item in archive.infolist() if item.filename == _BODY]
        if len(entries) != 1 or entries[0].file_size > _MAX_BODY_BYTES:
            raise ValueError("Expected one bounded Word body XML entry")
        with archive.open(entries[0]) as stream:
            raw = stream.read(_MAX_BODY_BYTES + 1)
    if len(raw) > _MAX_BODY_BYTES:
        raise ValueError("Word body exceeds the textbox replay bound")
    xml = raw.decode("utf-8-sig")
    if "\x00" in xml or "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        raise ValueError("Unsupported XML encoding or declaration in Word body")
    root = ET.fromstring(raw)
    for namespace in _WORD_NAMESPACES:
        body = root.find(namespace + "body")
        if root.tag == namespace + "document" and body is not None:
            return namespace + "txbxContent", body, digest(raw)
    raise ValueError("Expected a supported OOXML document body")


def _raw_textboxes(body: ET.Element, tag: str) -> dict[Branch, int]:
    counts: dict[Branch, int] = {"ordinary": 0, "choice": 0, "fallback": 0}
    pending: list[tuple[ET.Element, Branch]] = [(body, "ordinary")]
    while pending:
        node, branch = pending.pop()
        if node.tag == _MC + "Fallback":
            branch = "fallback"
        elif node.tag == _MC + "Choice" and branch != "fallback":
            branch = "choice"
        if node.tag == tag:
            counts[branch] += 1
        pending.extend((child, branch) for child in node)
    return counts


def snapshot(data: bytes) -> TextboxObservation:
    tag, body, xml_sha = _read_body(data)
    raw_counts = _raw_textboxes(body, tag)
    visited: Counter[WordStructureView] = Counter()
    report_support = WordStructureCollector.report_support

    def observe(
        collector: WordStructureCollector,
        path: tuple[int, ...],
        name: str,
        child_count: int,
        view: WordStructureView,
        *,
        recognized: bool = True,
    ) -> None:
        if collector.part == _BODY and name == tag:
            visited[view] += 1
        report_support(collector, path, name, child_count, view, recognized=recognized)

    native = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    with patch.object(WordStructureCollector, "report_support", observe):
        actual = native.extract_document_with_provenance(data)
    plain = native.extract_document(data)
    parts = actual.result.selected_parts
    if len(parts) != 1 or parts[0].word_view_status is None:
        raise ValueError("Native Word extraction did not retain its view status")
    if parts[0].word_view_status.known_walk != "finished":
        raise ValueError("Native Word extraction did not finish the legacy walk")
    if actual.document != plain:
        raise ValueError("Word provenance changed the legacy text or selection")
    if visited.total() > sum(raw_counts.values()):
        raise ValueError("Native textbox count exceeds the bounded body inventory")
    return {
        "input_sha256": digest(data),
        "body_xml_sha256": xml_sha,
        "legacy_text_sha256": digest(plain.text.encode()),
        "physical_manifest_sha256": actual.result.physical_manifest_sha256,
        "raw_textboxes": raw_counts,
        "visited_textboxes": dict(visited),
    }


def run(
    corpus: Path, catalog: Path, catalog_sha256: str, names: tuple[str, ...], output: Path
) -> None:
    root, target = corpus.resolve(strict=True), output.resolve()
    if output.is_symlink() or target.exists() or target.is_relative_to(root):
        raise ValueError("Output must be a new file outside the corpus")
    if not 1 <= len(names) <= _MAX_CASES or len(set(names)) != len(names):
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
    rows: dict[str, TextboxObservation] = {}
    with reads.guard():
        for name in sorted(names):
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
            rows[relative] = snapshot(data)
    if reads.changed_inputs() or before != source_fingerprints():
        raise ValueError("Textbox replay input or code changed during the run")
    write_json(
        target,
        {
            "suite": "word-textbox-walk-v1",
            "status": "recorded",
            "scope": "Bounded OOXML document.xml inventory and observed legacy body walk",
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
    args = parser.parse_args()
    run(args.corpus, args.catalog, args.catalog_sha256, tuple(args.case), args.output)


if __name__ == "__main__":
    main()

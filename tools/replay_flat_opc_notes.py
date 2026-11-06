"""Verify exact standalone Flat OPC footnote links on explicitly SHA-pinned local RCL files."""

import argparse
import re
from dataclasses import asdict
from pathlib import Path
from xml.etree import ElementTree as ET

from lexinform.adapters.document_text import FlatOpcNoteProof, WordXmlTextExtractor
from tools.corpus_artifacts import digest, write_json
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints

_PACKAGE = "{http://schemas.microsoft.com/office/2006/xmlPackage}"
_MAX_BYTES = 32_000_000
_MAX_CASES = 16


def _at(root: ET.Element, path: tuple[int, ...]) -> ET.Element:
    for index in path:
        if not 0 <= index < len(root):
            raise ValueError("Flat OPC proof path does not resolve inside its input")
        root = root[index]
    return root


def snapshot(raw: bytes, input_sha256: str) -> FlatOpcNoteProof:
    if len(raw) > _MAX_BYTES or digest(raw) != input_sha256:
        raise ValueError("Flat OPC input exceeds its bound or differs from the pinned SHA-256")
    reader = WordXmlTextExtractor()
    proof = reader.observe_flat_opc_notes(raw)
    if proof is None:
        raise ValueError("Flat OPC input has no bounded positive native note proof")
    if proof.input_sha256 != input_sha256 or proof.legacy_text_sha256 != digest(
        reader.extract(raw).encode()
    ):
        raise ValueError("Flat OPC proof disagrees with the input or the legacy text")
    root = ET.fromstring(raw)
    for link in proof.footnote_links:
        call = _at(root, link.call_path)
        paragraph = _at(root, link.paragraph_path)
        note = _at(root, link.definition_path)
        namespace, _, local = call.tag.rpartition("}")
        id_attribute = f"{namespace}}}id"
        if (
            local != "footnoteReference"
            or paragraph.tag != f"{namespace}}}p"
            or note.tag != f"{namespace}}}footnote"
            or link.call_path[: len(link.paragraph_path)] != link.paragraph_path
            or link.call_path == link.paragraph_path
            or call.get(id_attribute) != link.identity
            or note.get(id_attribute) != link.identity
            or _at(root, link.call_path[:1]).get(f"{_PACKAGE}name") != "/word/document.xml"
            or _at(root, link.definition_path[:1]).get(f"{_PACKAGE}name") != "/word/footnotes.xml"
        ):
            raise ValueError("Flat OPC proof does not resolve to the claimed package elements")
    return proof


def run(corpus: Path, cases: list[tuple[str, str]], output: Path) -> None:
    root, target = corpus.resolve(strict=True), output.resolve()
    if output.is_symlink() or target.exists() or target.is_relative_to(root):
        raise ValueError("Output must be a new file outside the corpus")
    names = {name for name, _ in cases}
    if not 1 <= len(cases) <= _MAX_CASES or len(names) != len(cases):
        raise ValueError("Expected 1–16 unique Flat OPC cases")
    if any(
        re.fullmatch(r"[0-9]{1,20}\.xml", name) is None
        or re.fullmatch(r"[0-9a-f]{64}", sha) is None
        for name, sha in cases
    ):
        raise ValueError("Expected a numeric XML basename and lowercase SHA-256")
    before = source_fingerprints()
    reads = CorpusReads(root)
    observations: dict[str, object] = {}
    with reads.guard():
        for name, sha in sorted(cases):
            relative = f"documents/files/{name}"
            path = root / relative
            if path.resolve() != path or not path.is_file() or path.stat().st_size > _MAX_BYTES:
                raise ValueError("Expected a regular, bounded corpus input with no path aliases")
            with path.open("rb") as stream:
                raw = stream.read(_MAX_BYTES + 1)
            observations[relative] = asdict(snapshot(raw, sha))
    if reads.changed_inputs() or before != source_fingerprints():
        raise ValueError("Flat OPC input or code changed during replay")
    write_json(
        target,
        {
            "suite": "flat-opc-note-proof-v1",
            "status": "recorded",
            "scope": "Standalone Flat OPC footnotes observed in the legacy Word body walk",
            "completeness": "not_asserted",
            "code_sha": code_version(),
            "source_sha256": before,
            "observations": observations,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument(
        "--case", required=True, action="append", nargs=2, metavar=("XML", "SHA256")
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(args.corpus, [(name, sha) for name, sha in args.case], args.output)


if __name__ == "__main__":
    main()

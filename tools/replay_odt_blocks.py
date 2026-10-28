"""Replay pinned ODT leaves through archive routing and verify their physical XML paths."""

import argparse
import io
import json
import logging
import zipfile
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree as ET

from lexinform.adapters.document_text import DocumentTextExtractor, OdtTextExtractor
from lexinform.models import OdtTextExtraction
from tools.corpus_artifacts import digest, observation_changes, read_artifact, write_json
from tools.corpus_manifests import RawReplayManifest
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints

Mode = Literal["library", "adapter"]
OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"


class RoutingOnly:
    def extract(self, data: bytes) -> str:
        return ""

    def pages(self, data: bytes) -> int:
        return 0

    def select_pages(self, data: bytes, *, first: int, count: int) -> bytes:
        return data


class LibraryObserver(RoutingOnly):
    def __init__(self) -> None:
        self.reader = OdtTextExtractor()
        self.payloads: dict[str, bytes] = {}
        self.attempts: list[dict[str, str | None]] = []

    def extract(self, data: bytes) -> str:
        identity = digest(data)
        self.payloads[identity] = data
        attempt: dict[str, str | None] = {
            "input_sha256": identity,
            "text_sha256": None,
            "error_type": None,
        }
        self.attempts.append(attempt)
        try:
            text = self.read(data)
        except Exception as exc:
            attempt["error_type"] = type(exc).__name__
            raise
        attempt["text_sha256"] = digest(text.encode())
        return text

    def read(self, data: bytes) -> str:
        return self.reader.extract(data)


class BlockObserver(LibraryObserver):
    def __init__(self) -> None:
        super().__init__()
        self.extractions: list[OdtTextExtraction] = []

    def read(self, data: bytes) -> str:
        extracted = self.reader.extract_odt_blocks(data)
        self.extractions.append(extracted)
        return extracted.legacy_text

    def extract_odt_blocks(self, data: bytes) -> OdtTextExtraction:
        self.extract(data)
        return self.extractions[-1]


def xml_node(body: ET.Element, path: tuple[int, ...]) -> ET.Element:
    node = body
    for index in path:
        node = node[index]
    return node


def verify_manifest(data: bytes, extracted: OdtTextExtraction) -> dict[str, Any]:
    restored = OdtTextExtraction.model_validate_json(extracted.model_dump_json())
    if restored.manifest_sha256 != extracted.manifest_sha256:
        raise ValueError("ODT manifest changes after a JSON round trip")
    if extracted.input_sha256 != digest(data):
        raise ValueError("ODT manifest refers to different input bytes")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.getinfo("content.xml").file_size > 32_000_000:
            raise ValueError("ODT XML exceeds the reader's bound")
        xml = archive.read("content.xml")
    if digest(xml) != extracted.xml_sha256:
        raise ValueError("ODT manifest refers to different XML bytes")
    if extracted.body_state == "read":
        body = ET.fromstring(xml).find(f"{OFFICE}body/{OFFICE}text")
        if body is None:
            raise ValueError("A read ODT body must exist")
        tags = {
            "paragraph": (TEXT + "p",),
            "heading": (TEXT + "h",),
            "table_row": (TABLE + "table-row",),
            "table_cell": (TABLE + "table-cell", TABLE + "covered-table-cell"),
        }
        for block in extracted.blocks:
            if xml_node(body, block.path).tag not in tags[block.kind]:
                raise ValueError("ODT block path does not address its declared XML element")
        for ignored in extracted.ignored_nodes:
            if xml_node(body, ignored.path).tag != ignored.tag:
                raise ValueError("Ignored ODT path does not address its declared XML element")
    return {
        "manifest_sha256": extracted.manifest_sha256,
        "xml_sha256": extracted.xml_sha256,
        "body_state": extracted.body_state,
        "verified_coordinates": len(extracted.blocks),
        "verified_ignored_coordinates": len(extracted.ignored_nodes),
        "text_view": extracted.text_view,
        "inline_notes": extracted.inline_notes,
        "tracked_changes": extracted.tracked_changes,
        "table_repetitions": extracted.table_repetitions,
    }


def snapshot(data: bytes, mode: Mode) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    observer = BlockObserver() if mode == "adapter" else LibraryObserver()
    routing = RoutingOnly()
    extracted = DocumentTextExtractor(routing, routing, routing, observer).extract_parts(
        data, include_pdf_pages=False, include_word_blocks=False, include_word_notes=False
    )
    core = {
        "input_sha256": digest(data),
        "attempts": observer.attempts,
        "parts": [part.model_dump(mode="json", exclude={"text"}) for part in extracted.parts],
        "rejected": list(extracted.rejected),
    }
    physical: list[dict[str, Any]] = []
    if isinstance(observer, BlockObserver):
        successful = sum(attempt["error_type"] is None for attempt in observer.attempts)
        if len(extracted.odt_blocks) != successful:
            raise ValueError("Every readable ODT leaf must retain its physical manifest")
        for link in extracted.odt_blocks:
            part = extracted.parts[link.part_index]
            manifest = link.extraction
            if manifest.input_sha256 != part.sha256 or manifest.legacy_text != part.text:
                raise ValueError("ODT manifest has an invalid physical part binding")
            physical.append(
                {
                    "part_index": link.part_index,
                    "members": part.members,
                    **verify_manifest(observer.payloads[part.sha256], manifest),
                }
            )
    return core, physical


def run(
    corpus: Path,
    cases: Path,
    output: Path,
    *,
    mode: Mode,
    reference: Path | None = None,
    reference_sha256: str | None = None,
) -> int:
    root, target = corpus.resolve(strict=True), output.resolve()
    if output.is_symlink() or target.exists() or target.is_relative_to(root):
        raise ValueError("Output must be a new file outside the read-only corpus")
    raw_manifest = read_artifact(cases)
    manifest = RawReplayManifest.model_validate_json(raw_manifest)
    paths = [case.path for case in manifest.cases]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate physical input paths")
    previous = None
    if mode == "adapter":
        if reference is None or reference_sha256 is None:
            raise ValueError("Adapter replay needs an explicit pinned library baseline")
        raw = read_artifact(reference)
        if digest(raw) != reference_sha256:
            raise ValueError("Reference SHA mismatch")
        previous = json.loads(raw)
        if previous.get("status") != "recorded" or previous.get("suite") != "odt-blocks-v1":
            raise ValueError("Reference must be a complete ODT baseline")
        if previous.get("case_manifest_sha256") != digest(raw_manifest):
            raise ValueError("Reference ODT population differs")
    elif reference is not None or reference_sha256 is not None:
        raise ValueError("Library baseline does not accept a reference")
    before = source_fingerprints()
    result: dict[str, Any] = {
        "suite": "odt-blocks-v1",
        "mode": mode,
        "code_sha": code_version(),
        "source_sha256": before,
        "case_manifest_sha256": digest(raw_manifest),
        "oracle": "machine_library_regression",
        "independent_legal_oracle": False,
        "exclusions": [
            *manifest.exclusions,
            "Non-ODT text/selection are excluded; PDF, DOC and DOCX adapters are placeholders.",
            "No independent legal oracle, pagination, note resolution or tracked-change decision.",
        ],
        "observations": {},
        "physical": {},
        "errors": {},
        "changes": [],
    }
    reads = CorpusReads(root)
    successful = 0
    with reads.guard():
        if digest((root / "INDEX.json").read_bytes()) != manifest.index_sha256:
            raise ValueError("Corpus index differs from the case manifest")
        for case in manifest.cases:
            try:
                path = root / case.path
                if Path(case.path).is_absolute() or not path.resolve().is_relative_to(root):
                    raise ValueError("ODT input escapes the corpus")
                data = path.read_bytes()
                if digest(data) != case.sha256:
                    raise ValueError("ODT input SHA mismatch")
                core, physical = snapshot(data, mode)
                result["observations"][case.path] = core
                result["physical"][case.path] = physical
                successful += sum(attempt["error_type"] is None for attempt in core["attempts"])
            except Exception as exc:
                result["errors"][case.path] = f"{type(exc).__name__}: {exc}"
    if not successful:
        result["errors"]["population"] = "Population contains no successfully observed ODT leaves"
    result["read_inputs"] = dict(sorted(reads.initial_sha256.items()))
    result["changed_inputs"] = reads.changed_inputs()
    result["changed_code"] = before != source_fingerprints()
    if previous is not None:
        result["reference_sha256"] = reference_sha256
        result["changes"] = observation_changes(previous["observations"], result["observations"])
    failed = (
        result["errors"] or result["changed_inputs"] or result["changed_code"] or result["changes"]
    )
    result["status"] = "failed" if failed else "passed" if previous is not None else "recorded"
    write_json(target, result)
    return int(bool(failed))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("library", "adapter"), required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--reference-sha256")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    raise SystemExit(
        run(
            args.corpus,
            args.cases,
            args.output,
            mode=args.mode,
            reference=args.reference,
            reference_sha256=args.reference_sha256,
        )
    )


if __name__ == "__main__":
    main()

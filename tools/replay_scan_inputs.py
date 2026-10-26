"""Replay pinned PDF scan windows and verify their physical page maps without model calls."""

import argparse
import base64
import gzip
import io
import json
import logging
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter, __version__
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    IndirectObject,
    NameObject,
    StreamObject,
)

import lexinform.services.documents as documents
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.errors import AttachmentTooLargeError
from lexinform.models import ScannedDocument
from lexinform.services.documents import TextLoader
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_health import local_path
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints


def inventory(corpus: Path, output: Path) -> None:
    reads = CorpusReads(corpus)
    rows: dict[str, Any] = {}
    with reads.guard():
        for record in sorted((corpus / "sejm/term10/prints").glob("*.json")):
            row = json.loads(record.read_bytes())
            number = row["number"]
            for attachment in row.get("attachments", []):
                name = attachment["name"]
                if name.lower().endswith(".pdf"):
                    path = local_path(corpus, f"sejm/term10/files/{number}__{name}")
                    rows[str(path.relative_to(corpus))] = {"family": "sejm", "id": str(number)}
        index = json.loads((corpus / "documents/index.json").read_bytes())
        for number, row in index.items():
            if row.get("extension") == "pdf" and row.get("chars") == 0:
                rows[f"documents/files/{number}.pdf"] = {"family": "rcl", "id": number}
        raster = json.loads(gzip.decompress((corpus / "page_raster.json.gz").read_bytes()))
        for identity, row in raster["files"].items():
            if identity.startswith("rpw:") and "S" in row.get("map", ""):
                number = identity.removeprefix("rpw:")
                rows[f"rpw/term10/files/{number}.pdf"] = {"family": "rpw", "id": number}
        for relative, row in rows.items():
            row["sha256"] = digest(local_path(corpus, relative).read_bytes())
    if not rows or reads.changed_inputs():
        raise ValueError("Scan inventory is empty or its inputs changed")
    write_json(output, {"version": 1, "cases": rows, "read_inputs": reads.initial_sha256})


def physical_object(value: Any, depth: int = 0) -> Any:
    if depth > 20:
        raise ValueError("PDF stream parameters exceed the recursion limit")
    if isinstance(value, IndirectObject):
        return physical_object(value.get_object(), depth + 1)
    if isinstance(value, StreamObject):
        output = io.BytesIO()
        value.write_to_stream(output)
        raw = output.getvalue().split(b"\nstream\n", 1)[1].removesuffix(b"\nendstream")
        return {
            "encoded_sha256": digest(raw),
            "parameters": {
                str(key): physical_object(child, depth + 1)
                for key, child in value.items()
                if key
                not in ("/Length", "/Resources", "/Metadata", "/StructParent", "/StructParents")
            },
        }
    if isinstance(value, DictionaryObject):
        return {str(key): physical_object(child, depth + 1) for key, child in value.items()}
    if isinstance(value, ArrayObject):
        return [physical_object(child, depth + 1) for child in value]
    return value.hex() if isinstance(value, bytes) else str(value)


def page_signatures(data: bytes) -> list[dict[str, Any]]:
    rows = []
    for page in PdfReader(io.BytesIO(data)).pages:
        contents = page.get_contents()
        resources = page.get("/Resources")
        objects = resources.get_object().get("/XObject") if resources is not None else None
        images = (
            {str(key): physical_object(value) for key, value in objects.get_object().items()}
            if objects is not None
            else {}
        )
        rows.append(
            {
                "box": [str(value) for value in page.mediabox],
                "rotation": page.rotation,
                "contents": digest(contents.get_data()) if contents is not None else None,
                "objects": images,
            }
        )
    return rows


def scan_view(scan: ScannedDocument | None) -> dict[str, Any] | None:
    if scan is None:
        return None
    data = base64.b64decode(scan.data)
    return {
        "source_sha256": scan.sha256,
        "input_sha256": digest(data),
        "input_bytes": len(data),
        "pages": scan.pages,
        "of_pages": scan.of_pages,
        "cover_letter_pages": scan.cover_letter_pages,
        "truncated": scan.truncated,
    }


def marked_prefix(data: bytes, text: str) -> bytes:
    writer, overlay = PdfWriter(), PdfWriter()
    for page in PdfReader(io.BytesIO(data)).pages[:3]:
        writer.add_page(page)
    index = min(1, len(writer.pages) - 1)
    target = writer.pages[index]
    mark = overlay.add_blank_page(
        width=float(target.mediabox.width), height=float(target.mediabox.height)
    )
    mark[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/FProbe"): DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Font"),
                            NameObject("/Subtype"): NameObject("/Type1"),
                            NameObject("/BaseFont"): NameObject("/Helvetica"),
                        }
                    )
                }
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /FProbe 12 Tf 10 10 Td ({text}) Tj ET".encode("ascii"))
    mark[NameObject("/Contents")] = stream
    target.merge_page(mark)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def mutation_probes(data: bytes) -> dict[str, bool]:
    result = {}
    for family, original, changed in (
        ("date", "Termin 30 dni", "Termin 31 dni"),
        ("amount", "Kwota 500 PLN", "Kwota 700 PLN"),
        ("negation", "Prawo przysluguje", "Prawo nie przysluguje"),
        ("scope", "Dotyczy obywateli", "Dotyczy cudzoziemcow"),
        ("reference", "art. 15 ust. 3", "art. 16 ust. 4"),
    ):
        left, right = marked_prefix(data, original), marked_prefix(data, changed)
        for cover in (False, True):
            a, _ = observe(left, cover=cover)
            b, _ = observe(right, cover=cover)
            previous = a["full"]
            shown = b["full"]
            pages = PdfReader(io.BytesIO(right)).pages
            visible = changed in (pages[min(1, len(pages) - 1)].extract_text() or "")
            result[f"{family}/cover={cover}"] = bool(
                shown and previous and shown["input_sha256"] != previous["input_sha256"] and visible
            )
    return result


def observe(data: bytes, *, cover: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    extractor = PypdfTextExtractor()

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        if max_bytes is not None and len(data) > max_bytes:
            raise AttachmentTooLargeError(url, max_bytes)
        return data

    loader = TextLoader({"scan.test": download}, extractor, max_bytes=32_000_000)
    scan = loader.load_scan("https://scan.test/document.pdf", cover_letter=cover)
    triage = loader.first_pages(scan, 8) if scan is not None else None
    metadata: dict[str, Any] = {}
    source_pages: list[dict[str, Any]] | None = None
    for name, shown in (("full", scan), ("triage", triage or scan)):
        if shown is None:
            continue
        manifest = shown.model_dump(mode="json", exclude={"data"}).get("scan_input")
        if manifest is not None:
            attached = base64.b64decode(shown.data)
            if manifest["source_sha256"] != digest(data) or manifest["input_sha256"] != digest(
                attached
            ):
                raise ValueError("Scan manifest does not identify the actual PDF bytes")
            if source_pages is None:
                source_pages = page_signatures(data)
            first, count = manifest["first_page"] - 1, manifest["page_count"]
            if page_signatures(attached) != source_pages[first : first + count]:
                raise ValueError("Attached PDF pages differ from the declared original window")
            metadata[name] = manifest
    return {"full": scan_view(scan), "triage": scan_view(triage or scan)}, metadata


def run(
    corpus: Path,
    manifest: Path,
    pin: str,
    output: Path,
    reference: Path | None,
    reference_pin: str | None,
) -> int:
    root = corpus.resolve(strict=True)
    if output.resolve().is_relative_to(root) or output.exists() or output.is_symlink():
        raise ValueError("Output must be new and outside the corpus")
    raw = read_artifact(manifest)
    if digest(raw) != pin:
        raise ValueError("Scan inventory SHA mismatch")
    cases = json.loads(raw)["cases"]
    if not cases:
        raise ValueError("Scan inventory must not be empty")
    previous = None
    if reference is not None:
        data = read_artifact(reference)
        if digest(data) != reference_pin:
            raise ValueError("Scan reference SHA mismatch")
        previous = json.loads(data)
        if previous["status"] != "recorded" or previous["case_manifest_sha256"] != pin:
            raise ValueError("Reference must be complete and use the same scan inventory")
    reads, before = CorpusReads(root), source_fingerprints()
    rows, metadata, errors, probes = {}, {}, {}, {}
    with reads.guard():
        for relative, row in cases.items():
            try:
                data = local_path(root, relative).read_bytes()
                if digest(data) != row["sha256"]:
                    raise ValueError("Raw PDF differs from pinned input")
                for cover in (False, True):
                    identity = f"{relative}/cover={cover}"
                    rows[identity], metadata[identity] = observe(data, cover=cover)
                if (
                    row["family"] not in probes
                    and len(data) < 1_000_000
                    and PypdfTextExtractor().pages(data) >= 2
                ):
                    probes[row["family"]] = {"source": relative, "checks": mutation_probes(data)}
            except Exception as exc:
                errors[relative] = f"{type(exc).__name__}: {exc}"
    changes = (
        [
            key
            for key in rows.keys() | previous["observations"].keys()
            if rows.get(key) != previous["observations"].get(key)
        ]
        if previous
        else []
    )
    changed_inputs = reads.changed_inputs()
    failed = bool(
        errors
        or changes
        or changed_inputs
        or before != source_fingerprints()
        or any(not value for probe in probes.values() for value in probe["checks"].values())
    )
    write_json(
        output,
        {
            "status": "failed" if failed else "passed" if previous else "recorded",
            "case_manifest_sha256": pin,
            "code_sha": code_version(),
            "source_sha256": before,
            "loader_sha256": digest(Path(documents.__file__).read_bytes()),
            "pypdf_version": __version__,
            "observations": rows,
            "scan_manifests": metadata,
            "errors": errors,
            "mutation_probes": probes,
            "changes": sorted(changes),
            "read_inputs": reads.initial_sha256,
            "changed_inputs": changed_inputs,
            "exclusions": [
                "Both cover settings are probes, not legal role or scan-routing decisions.",
                "Limits stay explicit; no OCR, provider calls or saved PDF derivatives.",
                "Page signatures cover content/XObject streams, boxes and rotation; no rendering.",
            ],
        },
    )
    return int(failed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--reference-sha256")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    if args.inventory:
        if args.output.resolve().is_relative_to(args.corpus.resolve()):
            parser.error("Inventory must stay outside the corpus")
        inventory(args.corpus.resolve(), args.output)
    elif args.manifest is None or args.manifest_sha256 is None:
        parser.error("Replay requires a pinned manifest")
    else:
        raise SystemExit(
            run(
                args.corpus,
                args.manifest,
                args.manifest_sha256,
                args.output,
                args.reference,
                args.reference_sha256,
            )
        )


if __name__ == "__main__":
    main()

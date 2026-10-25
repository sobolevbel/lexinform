"""Replay pinned raw PDFs through the legacy projection and physical page reader without network."""

import argparse
import hashlib
import io
import json
import logging
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.law_digest import law_digest
from lexinform.sections import trim_print
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints

PageReader = Literal["library", "adapter"]


class PdfReplayInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    official_ids: tuple[str, ...] = Field(min_length=1)
    cohorts: tuple[str, ...] = Field(min_length=1)


class PdfReplayManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    index_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cases: tuple[PdfReplayInput, ...] = Field(min_length=1)
    exclusions: tuple[str, ...] = Field(min_length=1)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def library_pages(data: bytes) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    error_type: str | None = None
    try:
        reader = PdfReader(io.BytesIO(data))
        for number, page in enumerate(reader.pages, 1):
            text, error = "", None
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                error = type(exc).__name__
            pages.append(
                {
                    "number": number,
                    "block_id": f"pdf-page:{number}:0",
                    "text_sha256": digest(text.encode()),
                    "error_type": error,
                }
            )
    except Exception as exc:
        error_type = type(exc).__name__
    return {
        "pages": pages,
        "page_count": len(pages) if error_type is None else None,
        "error_type": error_type,
    }


def snapshot(data: bytes, page_reader: PageReader) -> tuple[dict[str, Any], str | None]:
    pdf = PypdfTextExtractor()
    extractor = DocumentTextExtractor(pdf, DocxTextExtractor(), DocTextExtractor())
    document = extractor.extract_document(data)
    manifest_sha: str | None = None
    if page_reader == "library":
        physical = library_pages(data)
    else:
        parts = extractor.extract_parts(data)
        if len(parts.pdf_pages) != 1 or parts.pdf_pages[0].part_index != 0:
            raise ValueError("A raw PDF must expose page records through its physical part")
        extracted = parts.pdf_pages[0].extraction
        restored = type(extracted).model_validate_json(extracted.model_dump_json())
        if restored.manifest_sha256 != extracted.manifest_sha256:
            raise ValueError("Physical PDF manifest changes after a JSON round trip")
        manifest_sha = extracted.manifest_sha256
        physical = {
            "pages": [
                {
                    "number": page.number,
                    "block_id": page.block_id,
                    "text_sha256": page.text_sha256,
                    "error_type": page.error_type,
                }
                for page in extracted.pages
            ],
            "page_count": extracted.page_count,
            "error_type": extracted.error_type,
        }
        if extracted.input_sha256 != digest(data):
            raise ValueError("Physical PDF manifest refers to different bytes")
    trimmed = trim_print(document.text).text
    law = law_digest(trimmed)
    probes: dict[str, bool] = {}
    article = re.search(r"\bArt\.\s*1\s*[.)]", trimmed)
    if law is not None and article is not None:
        for name, text in (
            ("date", "Termin: 17 października 2027 r."),
            ("amount", "Kwota wynosi 500 zł."),
            ("negation", "Nie przysługuje prawo do świadczenia."),
            ("scope", "Przepisy stosuje się także do cudzoziemców."),
            ("reference", "Stosuje się art. 15 ust. 3 ustawy."),
        ):
            changed = trimmed[: article.end()] + " " + text + " " + trimmed[article.end() :]
            probes[name] = law_digest(changed) != law
    return {
        "input_sha256": digest(data),
        "bytes": len(data),
        "text_sha256": digest(document.text.encode()),
        "selection_sha256": digest(document.selection.model_dump_json().encode()),
        "selection_state": document.selection.state,
        "law_digest": law,
        "mutations": probes,
        "physical": physical,
    }, manifest_sha


def run(
    corpus: Path,
    cases: Path,
    output: Path,
    *,
    page_reader: PageReader,
    reference: Path | None = None,
    reference_sha256: str | None = None,
) -> int:
    root, target = corpus.resolve(strict=True), output.resolve()
    if output.is_symlink() or target.exists() or target.is_relative_to(root):
        raise ValueError("Output must be a new file outside the read-only corpus")
    if cases.name == ".env" or cases.resolve().name == ".env":
        raise ValueError("A credential file is not a replay manifest")
    if reference is not None and (reference.name == ".env" or reference.resolve().name == ".env"):
        raise ValueError("A credential file is not a replay reference")
    raw_manifest = cases.read_bytes()
    manifest = PdfReplayManifest.model_validate_json(raw_manifest)
    paths = [case.path for case in manifest.cases]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate physical input paths")
    previous = None
    if reference is not None:
        raw = reference.read_bytes()
        if digest(raw) != reference_sha256:
            raise ValueError("Reference SHA mismatch")
        previous = json.loads(raw)
        if previous.get("status") != "recorded" or previous.get("suite") != "pdf-pages-v1":
            raise ValueError("Reference must be a complete PDF pages baseline")
        if previous.get("case_manifest_sha256") != digest(raw_manifest):
            raise ValueError("Reference PDF population differs")
    elif reference_sha256 is not None or page_reader != "library":
        raise ValueError("Adapter replay needs an explicit pinned library baseline")
    before = source_fingerprints()
    result: dict[str, Any] = {
        "suite": "pdf-pages-v1",
        "code_sha": code_version(),
        "source_sha256": before,
        "case_manifest_sha256": digest(raw_manifest),
        "oracle": "machine_library_observation",
        "independent_legal_oracle": False,
        "exclusions": manifest.exclusions,
        "page_reader": page_reader,
        "observations": {},
        "page_manifests": {},
        "errors": {},
        "changes": [],
    }
    reads = CorpusReads(root)
    with reads.guard():
        if digest((root / "INDEX.json").read_bytes()) != manifest.index_sha256:
            raise ValueError("Corpus index differs from the case manifest")
        for case in manifest.cases:
            try:
                path = root / case.path
                if Path(case.path).is_absolute() or not path.resolve().is_relative_to(root):
                    raise ValueError("PDF input escapes the corpus")
                data = path.read_bytes()
                if digest(data) != case.sha256:
                    raise ValueError("PDF input SHA mismatch")
                actual, page_manifest = snapshot(data, page_reader)
                result["observations"][case.path] = actual
                if page_manifest is not None:
                    result["page_manifests"][case.path] = page_manifest
            except Exception as exc:
                result["errors"][case.path] = f"{type(exc).__name__}: {exc}"
    result["changed_inputs"] = [
        name
        for name, initial in reads.initial_sha256.items()
        if not (root / name).is_file() or digest((root / name).read_bytes()) != initial
    ]
    result["changed_code"] = before != source_fingerprints()
    if previous is not None:
        result["reference_sha256"] = reference_sha256
        result["changes"] = [
            name
            for name in sorted(result["observations"].keys() | previous["observations"].keys())
            if result["observations"].get(name) != previous["observations"].get(name)
        ]
    failed = (
        result["errors"] or result["changed_inputs"] or result["changed_code"] or result["changes"]
    )
    result["status"] = "failed" if failed else "passed" if previous is not None else "recorded"
    with output.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    return int(bool(failed))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--page-reader", choices=("library", "adapter"), required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--reference-sha256")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    raise SystemExit(
        run(
            args.corpus,
            args.cases,
            args.output,
            page_reader=args.page_reader,
            reference=args.reference,
            reference_sha256=args.reference_sha256,
        )
    )


if __name__ == "__main__":
    main()

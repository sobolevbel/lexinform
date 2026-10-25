"""Compare raw-corpus prepared text and input coverage against an explicit committed baseline."""

import argparse
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Literal

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.document_selection import bill_selection, selection_for_input
from lexinform.law_digest import law_digest
from lexinform.sections import TextBudget, trim_print
from tools.corpus_check import code_version, source_fingerprints
from tools.corpus_external import CorpusReads
from tools.replay_pdf_pages import PdfReplayManifest


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_artifact(path: Path) -> bytes:
    if path.name == ".env" or path.resolve().name == ".env":
        raise ValueError("A credential file is not a replay artifact")
    return path.read_bytes()


def snapshot(data: bytes, *, mapped: bool) -> dict[str, Any]:
    reader = DocumentTextExtractor(
        PypdfTextExtractor(),
        DocxTextExtractor(),
        DocTextExtractor(),
        max_member_bytes=32_000_000,
        max_part_chars=400_000,
    )
    extracted = reader.extract_document(data)
    selected = bill_selection(extracted.selection, source_basis=())
    prepared = trim_print(extracted.text)
    variants = {"trim": prepared.text}
    variants.update(
        {str(limit): TextBudget(limit).apply(prepared.text).text for limit in (1000, 24000)}
    )
    selections = {key: selection_for_input(selected, text) for key, text in variants.items()}
    projection = getattr(prepared, "projection", None)
    mapping = None
    if mapped:
        if projection is None or projection.source_sha256 != digest(extracted.text.encode()):
            raise ValueError("Prepared text must expose a projection of the exact extracted source")
        chunks = []
        offset = 0
        generated = 0
        for segment in projection.segments:
            if segment.start != offset:
                raise ValueError("Projection does not partition the prepared input")
            text = prepared.text[segment.start : segment.end]
            if segment.source_start is None:
                generated += len(text)
            else:
                original = extracted.text[segment.source_start : segment.source_start + len(text)]
                if original != text:
                    raise ValueError("Projection source range differs from the emitted text")
            chunks.append(text)
            offset = segment.end
        if "".join(chunks) != prepared.text or offset != len(prepared.text):
            raise ValueError("Projection round trip changed the prepared input")
        mapping = {
            "segments": len(projection.segments),
            "generated_chars": generated,
            "round_trip": True,
        }
    return {
        "core": {
            "input_sha256": digest(data),
            "extracted_sha256": digest(extracted.text.encode()),
            "selection_sha256": digest(extracted.selection.model_dump_json().encode()),
            "law_digest": law_digest(prepared.text),
            "dropped": [[section.name, section.chars] for section in prepared.dropped],
            "inputs": {key: digest(text.encode()) for key, text in variants.items()},
            "decisions": {
                key: {"state": value.state, "limitations": list(value.limitations)}
                for key, value in selections.items()
            },
        },
        "coverage": {
            key: [span.model_dump(mode="json") for span in value.coverage]
            for key, value in selections.items()
        },
        "selection_json_bytes": {
            key: len(value.without_text().model_dump_json().encode())
            for key, value in selections.items()
        },
        "mapping": mapping,
    }


def run(
    corpus: Path,
    catalog: Path,
    catalog_sha256: str,
    pdf_cases: Path,
    output: Path,
    *,
    mode: Literal["baseline", "compare"],
    reference: Path | None = None,
    reference_sha256: str | None = None,
) -> int:
    root, target = corpus.resolve(strict=True), output.resolve()
    if output.is_symlink() or target.exists() or target.is_relative_to(root):
        raise ValueError("Output must be a new file outside the corpus")
    raw_catalog, raw_pdf = read_artifact(catalog), read_artifact(pdf_cases)
    if digest(raw_catalog) != catalog_sha256:
        raise ValueError("Input catalog SHA mismatch")
    inventory = json.loads(raw_catalog)
    if (
        inventory.get("suite") != "external"
        or inventory.get("status") != "recorded"
        or inventory.get("requested_profiles") != ["extraction"]
    ):
        raise ValueError("Input catalog must be a complete extraction baseline")
    manifest = PdfReplayManifest.model_validate_json(raw_pdf)
    inputs = {
        "documents/files/" + Path(key).name: row["actual"]["sha256"]
        for key, row in inventory["observations"].items()
        if key.startswith(("word/", "documents/packages/"))
    }
    for case in manifest.cases:
        if case.path in inputs:
            raise ValueError("Duplicate raw input")
        inputs[case.path] = case.sha256
    previous = None
    if mode == "compare":
        if reference is None or reference_sha256 is None:
            raise ValueError("Compare requires a pinned baseline")
        raw = read_artifact(reference)
        if digest(raw) != reference_sha256:
            raise ValueError("Reference SHA mismatch")
        previous = json.loads(raw)
        if (
            previous.get("suite") != "input-coverage-v1"
            or previous.get("status") != "recorded"
            or previous.get("catalog_sha256") != catalog_sha256
            or previous.get("pdf_manifest_sha256") != digest(raw_pdf)
        ):
            raise ValueError("Reference must declare the same complete raw population")
    elif reference is not None or reference_sha256 is not None:
        raise ValueError("Baseline does not accept a reference")
    before = source_fingerprints()
    result: dict[str, Any] = {
        "suite": "input-coverage-v1",
        "code_sha": code_version(),
        "source_sha256": before,
        "catalog_sha256": catalog_sha256,
        "pdf_manifest_sha256": digest(raw_pdf),
        "oracle": "machine_regression",
        "independent_legal_oracle": False,
        "exclusions": [
            "No paid calls, actual provider tokenization or production history replay.",
            "Exact ranges require ordered assembly or traced trim; other transforms are unknown.",
            "Source admissibility has no new official relations or independent legal oracle.",
        ],
        "observations": {},
        "errors": {},
        "core_changes": [],
        "coverage_changes": [],
    }
    reads = CorpusReads(root)
    with reads.guard():
        if digest((root / "INDEX.json").read_bytes()) != manifest.index_sha256:
            raise ValueError("Corpus index changed")
        for name, sha in sorted(inputs.items()):
            try:
                path = (root / name).resolve()
                if not path.is_relative_to(root):
                    raise ValueError("Input escapes corpus")
                data = path.read_bytes()
                if digest(data) != sha:
                    raise ValueError("Raw input SHA mismatch")
                result["observations"][name] = snapshot(data, mapped=mode == "compare")
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
        for name in sorted(previous["observations"].keys() | result["observations"].keys()):
            old, new = previous["observations"].get(name), result["observations"].get(name)
            if old is None or new is None or old["core"] != new["core"]:
                result["core_changes"].append(name)
            if old is not None and new is not None and old["coverage"] != new["coverage"]:
                result["coverage_changes"].append(name)
    failed = bool(
        result["errors"]
        or result["changed_inputs"]
        or result["changed_code"]
        or result["core_changes"]
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
    parser.add_argument("--pdf-cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("baseline", "compare"), required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--reference-sha256")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    raise SystemExit(
        run(
            args.corpus,
            args.catalog,
            args.catalog_sha256,
            args.pdf_cases,
            args.output,
            mode=args.mode,
            reference=args.reference,
            reference_sha256=args.reference_sha256,
        )
    )


if __name__ == "__main__":
    main()

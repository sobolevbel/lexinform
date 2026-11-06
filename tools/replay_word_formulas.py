"""Audit Office Math source structures on SHA-pinned Word bodies offline."""

import argparse
import json
import zipfile
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.word_formulas import read_word_formulas
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints
from tools.replay_proposal_edition import PinnedInput

_MAX_BYTES = 32_000_000


class WordFormulaManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["word-omml-source-v1"] = "word-omml-source-v1"
    inputs: tuple[PinnedInput, ...] = Field(min_length=1, max_length=512)


def replay(corpus: Path, manifest: WordFormulaManifest) -> dict[str, object]:
    root = corpus.resolve(strict=True)
    reads, before = CorpusReads(root), source_fingerprints()
    reader = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    rows: dict[str, object] = {}
    counts: Counter[str] = Counter()
    with reads.guard():
        for item in manifest.inputs:
            if item.path in rows:
                raise ValueError("Formula replay inputs must have distinct paths")
            data = item.read(root)
            if len(data) > _MAX_BYTES:
                raise ValueError("Pinned Word body exceeds the replay limit")
            scan = read_word_formulas(data)
            ordinary = reader.extract_document(data)
            formulas = []
            for formula in scan.formulas:
                structure = formula.structure
                checksum = (
                    digest(json.dumps(asdict(structure), sort_keys=True).encode())
                    if structure is not None
                    else None
                )
                counts[formula.unknown or "structured"] += 1
                formulas.append(
                    {
                        "path": formula.path,
                        "paragraph_path": formula.paragraph_path,
                        "xml_sha256": formula.xml_sha256,
                        "ordered_structure_sha256": checksum,
                        "unknown": formula.unknown,
                    }
                )
            rows[item.path] = {
                "coverage": scan.coverage,
                "document_xml_sha256": scan.document_xml_sha256,
                "formulas": formulas,
                "legacy_text_sha256": digest(ordinary.text.encode()),
                "selection_sha256": digest(
                    json.dumps(
                        ordinary.selection.model_dump(mode="json"),
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode()
                ),
            }
    if reads.changed_inputs() or before != source_fingerprints():
        raise ValueError("Replay inputs or code changed")
    return {
        "version": manifest.version,
        "status": "recorded",
        "scope": "Office Math source structure in the Word main XML body.",
        "rendered_view": "unresolved",
        "observations": rows,
        "formula_counts": dict(counts),
        "read_inputs": reads.initial_sha256,
        "code_version": code_version(),
        "source_pins_sha256": digest(json.dumps(before, sort_keys=True).encode()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.resolve().is_relative_to(args.corpus.resolve()):
            raise ValueError("Replay output must be outside the corpus")
        raw = read_artifact(args.manifest)
        if digest(raw) != args.manifest_sha256:
            raise ValueError("Formula manifest SHA differs")
        result = replay(args.corpus, WordFormulaManifest.model_validate_json(raw))
        write_json(args.output, {**result, "manifest_sha256": args.manifest_sha256})
    except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()

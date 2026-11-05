"""Compare raw-corpus prepared text and input coverage against an explicit committed baseline."""

import argparse
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from lexinform import sections
from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.document_selection import bill_selection, selection_for_input
from lexinform.keywords import KeywordPrefilter
from lexinform.law_digest import law_digest
from lexinform.models import (
    DocumentSelection,
    DocumentSelectionRecord,
    ExtractionResult,
    PreparedTextInput,
)
from lexinform.prepared_input import prepared_text_input
from lexinform.sections import PAGE_BREAK, TextBudget, trim_print
from lexinform.text_projection import TextProjection
from tools.corpus_artifacts import digest as digest
from tools.corpus_artifacts import read_artifact as read_artifact
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints
from tools.replay_pdf_pages import PdfReplayManifest


def coverage_views(
    selected: DocumentSelection,
    *,
    extractions: tuple[ExtractionResult, ...] = (),
    inputs: Mapping[str, PreparedTextInput] | None = None,
) -> dict[str, Any]:
    """Report independent physical, selection and emitted-text evidence for one replay."""
    if any(span.stage == "analysis" for span in selected.coverage):
        raise ValueError("Coverage views require the selection before input fitting")
    selection_id = DocumentSelectionRecord.from_selection(selected).selection_id
    emitted: dict[str, Any] = {}
    for name, manifest in (inputs or {}).items():
        if manifest.source_selection_id != selection_id:
            raise ValueError("Prepared coverage differs from its source selection")
        chars = {"source": 0, "generated": 0, "unknown": 0}
        copied_chars: dict[int, int] = {}
        for segment in manifest.segments:
            size = segment.end - segment.start
            chars[segment.origin] += size
            if segment.part_index is not None:
                index = segment.part_index
                copied_chars[index] = copied_chars.get(index, 0) + size
        emitted[name] = {
            "input_id": manifest.input_id,
            "mapping": manifest.mapping,
            "coordinates": manifest.coordinates,
            "text_chars": manifest.text_chars,
            "chars_by_origin": chars,
            "copied_chars_by_part": [
                {
                    "part_index": index,
                    "role": manifest.parts[index].role,
                    "chars": copied_chars[index],
                }
                for index in sorted(copied_chars)
            ],
        }
    status = "unknown" if inputs is None else "prepared" if emitted else "not_prepared"
    return {
        "extraction": {
            "evidence": "observed_artifacts" if extractions else "unknown",
            "artifacts": [
                {
                    "result_id": item.result_id,
                    "artifact_sha256": item.artifact_sha256,
                    "physical_part_count": item.physical_part_count,
                    "selected_part_count": item.selected_part_count,
                    "omitted_selected_parts": item.omitted_selected_parts,
                    "completeness": item.completeness,
                }
                for item in extractions
            ],
        },
        "selection": {
            "selection_id": selection_id,
            "state": selected.state,
            "completeness": list(selected.completeness),
            "limitations": list(selected.limitations),
        },
        "emitted": {"status": status, "variants": emitted},
    }


def snapshot(
    data: bytes,
    *,
    mapped: bool,
    prepared_manifest: bool = False,
    extraction_manifest: bool = False,
    include_coverage_views: bool = False,
) -> dict[str, Any]:
    if prepared_manifest and not mapped:
        raise ValueError("Prepared manifests require mapped replay")
    if include_coverage_views and not mapped:
        raise ValueError("Coverage views require mapped replay")
    reader = DocumentTextExtractor(
        PypdfTextExtractor(),
        DocxTextExtractor(),
        DocTextExtractor(),
        max_member_bytes=32_000_000,
        max_part_chars=400_000,
    )
    provenance = (
        reader.extract_document_with_provenance(data)
        if extraction_manifest or include_coverage_views
        else None
    )
    extracted = provenance.document if provenance is not None else reader.extract_document(data)
    selected = bill_selection(extracted.selection, source_basis=())
    prepared = trim_print(extracted.text)
    keywords = KeywordPrefilter()
    spans = keywords.spans(prepared.text)
    budgets = {str(limit): TextBudget(limit).apply(prepared.text) for limit in (1000, 24000)}
    budgets.update(
        {
            f"keywords-{limit}": TextBudget(limit).apply(prepared.text, spans)
            for limit in (1000, 24000)
        }
    )
    variants = {"trim": prepared.text}
    variants.update({key: budget.text for key, budget in budgets.items()})
    variants["triage"] = sections.excerpts(prepared.text, spans)
    fitting = (("cost-1", "keywords-24000", 600, 2400), ("cost-2", "cost-1", 300, 1200))
    for name, parent, head, maximum in fitting:
        variants[name] = sections.excerpts(
            variants[parent], keywords.spans(variants[parent]), head_chars=head, max_chars=maximum
        )
    projection = getattr(prepared, "projection", None)
    mapping = None
    projections: dict[str, TextProjection] = {}
    if mapped:
        if projection is None or projection.source_sha256 != digest(extracted.text.encode()):
            raise ValueError("Prepared text must expose a projection of the exact extracted source")
        projections = {"trim": projection}
        for key, budget in budgets.items():
            if budget.projection is None:
                raise ValueError("TextBudget must expose an exact projection")
            projections[key] = budget.projection.compose(projection)
        projections["triage"] = sections.excerpt_projection(prepared.text, spans).compose(
            projection
        )
        for name, parent, head, maximum in fitting:
            projections[name] = sections.excerpt_projection(
                variants[parent],
                keywords.spans(variants[parent]),
                head_chars=head,
                max_chars=maximum,
            ).compose(projections[parent])
        maps = {}
        for name, trace in projections.items():
            if trace.text != variants[name]:
                raise ValueError("Traced transformation changed its emitted text")
            maps[name] = check_projection(trace, extracted.text)
        mapping = {**maps["trim"], "variants": maps}
    assembly_matches = PAGE_BREAK.join(part.text for part in selected.parts) == extracted.text
    selections = {}
    for name, text in variants.items():
        input_trace = projections.get(name) if assembly_matches else None
        selections[name] = (
            selection_for_input(selected, text, projection=input_trace)
            if input_trace is not None
            else selection_for_input(selected, text)
        )
    result = {
        "core": {
            "input_sha256": digest(data),
            "extracted_sha256": digest(extracted.text.encode()),
            "selection_sha256": digest(extracted.selection.model_dump_json().encode()),
            "law_digest": law_digest(prepared.text),
            "dropped": [[section.name, section.chars] for section in prepared.dropped],
            "inputs": {key: digest(text.encode()) for key, text in variants.items()},
            "truncated": {key: budget.truncated for key, budget in budgets.items()},
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
    if prepared_manifest or include_coverage_views:
        manifests = {
            name: prepared_text_input(
                selected, text, projection=projections.get(name) if assembly_matches else None
            )
            for name, text in variants.items()
        }
        checked = {
            name: check_prepared_input(manifest, selected, variants[name])
            for name, manifest in manifests.items()
        }
        if prepared_manifest:
            result["prepared_inputs"] = checked
        if include_coverage_views:
            result["coverage_views"] = coverage_views(
                selected,
                extractions=(provenance.result,) if provenance is not None else (),
                inputs=manifests,
            )
    if provenance is not None and extraction_manifest:
        encoded = provenance.result.model_dump_json()
        restored = ExtractionResult.model_validate_json(encoded)
        if restored != provenance.result or restored.result_id != provenance.result.result_id:
            raise ValueError("Extraction metadata identity changed on round trip")
        if (
            restored.artifact_sha256 != digest(data)
            or restored.output_text_sha256 != digest(extracted.text.encode())
            or restored.output_chars != len(extracted.text)
        ):
            raise ValueError("Extraction metadata differs from its observed input or output")
        result["extraction"] = {
            "result_id": restored.result_id,
            "metadata_bytes": len(encoded.encode()),
            "manifest": restored.model_dump(mode="json"),
        }
    return result


def check_prepared_input(
    manifest: PreparedTextInput, selection: DocumentSelection, text: str
) -> dict[str, Any]:
    encoded = manifest.model_dump_json()
    restored = PreparedTextInput.model_validate_json(encoded)
    if restored != manifest or restored.input_id != manifest.input_id:
        raise ValueError("Prepared manifest identity changed on round trip")
    if manifest.text_sha256 != digest(text.encode()) or manifest.text_chars != len(text):
        raise ValueError("Prepared manifest differs from the emitted text")
    for segment in manifest.segments:
        if segment.origin != "source":
            continue
        index = segment.part_index
        assert index is not None, "PreparedTextSegment requires a source part"
        copied = selection.parts[index].text[segment.source_start : segment.source_end]
        if copied != text[segment.start : segment.end]:
            raise ValueError("Prepared source part range differs from the emitted text")
    return {"input_id": manifest.input_id, "manifest": manifest.model_dump(mode="json")}


def check_projection(projection: TextProjection, source: str) -> dict[str, Any]:
    if projection.source_sha256 != digest(source.encode()) or projection.source_length != len(
        source
    ):
        raise ValueError("Projection input identity differs from the extracted source")
    offset, generated = 0, 0
    chunks = []
    for segment in projection.segments:
        if segment.start != offset:
            raise ValueError("Projection does not partition the prepared input")
        text = projection.text[segment.start : segment.end]
        if segment.source_start is None:
            generated += len(text)
        elif source[segment.source_start : segment.source_start + len(text)] != text:
            raise ValueError("Projection source range differs from the emitted text")
        chunks.append(text)
        offset = segment.end
    if "".join(chunks) != projection.text or offset != len(projection.text):
        raise ValueError("Projection round trip changed the prepared input")
    return {"segments": len(projection.segments), "generated_chars": generated, "round_trip": True}


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
    prepared_manifest: bool = False,
    extraction_manifest: bool = False,
    include_coverage_views: bool = False,
) -> int:
    if prepared_manifest and mode != "compare":
        raise ValueError("Prepared manifests require mapped compare mode")
    if include_coverage_views and mode != "compare":
        raise ValueError("Coverage views require mapped compare mode")
    if extraction_manifest and mode != "compare":
        raise ValueError("Extraction manifests require pinned compare mode")
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
            previous.get("suite") != "input-coverage-v2"
            or previous.get("status") != "recorded"
            or previous.get("catalog_sha256") != catalog_sha256
            or previous.get("pdf_manifest_sha256") != digest(raw_pdf)
        ):
            raise ValueError("Reference must declare the same complete raw population")
    elif reference is not None or reference_sha256 is not None:
        raise ValueError("Baseline does not accept a reference")
    before = source_fingerprints()
    result: dict[str, Any] = {
        "suite": "input-coverage-v2",
        "code_sha": code_version(),
        "source_sha256": before,
        "catalog_sha256": catalog_sha256,
        "pdf_manifest_sha256": digest(raw_pdf),
        "oracle": "machine_regression",
        "independent_legal_oracle": False,
        "exclusions": [
            "No paid calls, actual provider tokenization or production history replay.",
            "Runtime budget maps are wired; cost fitting/amendment assembly remain unknown.",
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
                result["observations"][name] = snapshot(
                    data,
                    mapped=mode == "compare",
                    prepared_manifest=prepared_manifest,
                    extraction_manifest=extraction_manifest,
                    include_coverage_views=include_coverage_views,
                )
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
    parser.add_argument("--prepared-manifest", action="store_true")
    parser.add_argument("--extraction-manifest", action="store_true")
    parser.add_argument("--coverage-views", action="store_true")
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
            prepared_manifest=args.prepared_manifest,
            extraction_manifest=args.extraction_manifest,
            include_coverage_views=args.coverage_views,
        )
    )


if __name__ == "__main__":
    main()

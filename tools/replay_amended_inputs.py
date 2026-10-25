"""Replay SHA-pinned RCL self-amendment assemblies from cached text without network access."""

import argparse
import json
import logging
from importlib import import_module
from pathlib import Path
from typing import Any

from lexinform.document_selection import selection_for_input
from lexinform.keywords import KeywordPrefilter
from lexinform.models import TextDocument
from lexinform.sections import PAGE_BREAK, TextBudget, trim_print
from lexinform.services.documents import TextLoader
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.corpus_rcl import RequiredCachedExtractor
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints
from tools.replay_document_candidates import document_id
from tools.replay_input_coverage import check_projection


def snapshot(document: TextDocument, loader: TextLoader, *, mapped: bool) -> dict[str, Any]:
    selected, file = loader.select_document(document)
    base: dict[str, Any] = {
        "document": selected.model_dump(mode="json"),
        "selection_sha256": digest(file.selection.model_dump_json().encode()),
        "state": file.selection.state,
    }
    if file.text is None:
        return {"core": base, "coverage": {}, "mapping": {}, "selection_json_bytes": {}}
    trimmed = trim_print(file.text)
    amendments = tuple(part for part in file.selection.parts if part.role == "self_amendment")
    combined = trimmed.text
    if amendments:
        combined += (
            "\n\nAUTOPOPRAWKI: odrębne propozycje zmian "
            "do powyższego projektu; nie tekst jednolity.\n"
        )
        combined += "\n\n".join(f"Źródło: {part.url}\n{part.text}" for part in amendments)
    projection = None
    if mapped:
        assembly = import_module("lexinform.document_input")
        rendered = assembly.append_self_amendments(trimmed.text, amendments)
        if rendered != combined:
            raise ValueError("Amendment rendering changed the legacy input")
        if trimmed.projection is not None:
            projection = assembly.amended_input_projection(
                file.selection, trimmed.projection, combined
            )
    variants = {"full": combined}
    projections = {"full": projection}
    spans = KeywordPrefilter().spans(combined)
    for limit in (1000, 24000):
        for name, selected_spans in ((str(limit), ()), (f"keywords-{limit}", spans)):
            budget = TextBudget(limit).apply(combined, selected_spans)
            variants[name] = budget.text
            projections[name] = (
                budget.projection.compose(projection)
                if projection is not None and budget.projection is not None
                else None
            )
    selections, maps = {}, {}
    source = PAGE_BREAK.join(part.text for part in file.selection.parts)
    for name, text in variants.items():
        trace = projections[name]
        selections[name] = selection_for_input(file.selection, text, projection=trace)
        if trace is not None:
            if trace.text != text:
                raise ValueError("Amendment map differs from the prepared input")
            maps[name] = check_projection(trace, source)
    base.update(
        {
            "file_text_sha256": digest(file.text.encode()),
            "inputs": {name: digest(text.encode()) for name, text in variants.items()},
            "dropped": [(part.name, part.chars) for part in trimmed.dropped],
            "decisions": {
                name: {"state": item.state, "limitations": item.limitations}
                for name, item in selections.items()
            },
        }
    )
    return {
        "core": base,
        "coverage": {
            name: [span.model_dump(mode="json") for span in item.coverage]
            for name, item in selections.items()
        },
        "mapping": maps,
        "selection_json_bytes": {
            name: len(item.without_text().model_dump_json().encode())
            for name, item in selections.items()
        },
    }


def run(corpus: Path, inventory: Path, inventory_sha256: str, output: Path, *, mapped: bool) -> int:
    root, target = corpus.resolve(strict=True), output.resolve()
    if output.is_symlink() or target.exists() or target.is_relative_to(root):
        raise ValueError("Output must be new and outside the corpus")
    raw = read_artifact(inventory)
    if digest(raw) != inventory_sha256:
        raise ValueError("Amendment inventory SHA mismatch")
    population = json.loads(raw)
    before = source_fingerprints()
    reads = CorpusReads(root)
    loader = TextLoader(
        {"legislacja.rcl.gov.pl": document_id},
        RequiredCachedExtractor(root, reads),
        max_bytes=32_000_000,
        cache_size=256,
    )
    result: dict[str, Any] = {
        "suite": "amended-input-v1",
        "code_sha": code_version(),
        "source_sha256": before,
        "inventory_sha256": inventory_sha256,
        "observations": {},
        "errors": {},
        "exclusions": [
            "Cached text offsets, not raw-byte or page coordinates.",
            "No new source policy, model calls or independent legal oracle.",
        ],
    }
    with reads.guard():
        for name, sha in population["read_inputs"].items():
            path = (root / name).resolve()
            if not path.is_relative_to(root) or digest(path.read_bytes()) != sha:
                raise ValueError("Candidate metadata changed or escapes corpus")
        for identity, row in population["cases"].items():
            try:
                result["observations"][identity] = snapshot(
                    TextDocument.model_validate(row["document"]), loader, mapped=mapped
                )
            except Exception as exc:
                result["errors"][identity] = f"{type(exc).__name__}: {exc}"
    result["changed_inputs"] = [
        name
        for name, sha in reads.initial_sha256.items()
        if ((root / name).is_file() and digest((root / name).read_bytes()) != sha)
        or (sha is not None and not (root / name).is_file())
    ]
    result["changed_code"] = before != source_fingerprints()
    result["read_inputs"] = reads.initial_sha256
    result["missing_inputs"] = sorted(reads.missing)
    failed = bool(result["errors"] or result["changed_inputs"] or result["changed_code"])
    result["status"] = "failed" if failed else "recorded"
    write_json(target, result)
    return int(failed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mapped", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    raise SystemExit(
        run(args.corpus, args.inventory, args.inventory_sha256, args.output, mapped=args.mapped)
    )


if __name__ == "__main__":
    main()

"""Replay raw selected text through public fake-backed analysis and verify cost-fitting ranges."""

import argparse
import json
import logging
import os
from collections.abc import Sequence
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tests.fakes import FakeBatchBackend, FakeLlm, FakeTextExtractor
from tests.harness import World

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models import BillContext
from lexinform.models.batch import BatchItemMeta
from lexinform.sections import TextBudget, document_kind, excerpt_projection, trim_print
from lexinform.services import analysis as analysis_module
from lexinform.text_projection import TextProjection
from tools.corpus_artifacts import digest, read_artifact
from tools.corpus_runtime import CorpusReads, code_version, source_fingerprints
from tools.replay_input_coverage import check_projection


def runtime_snapshot(text: str, *, batch: bool, mapped: bool) -> dict[str, Any]:
    counted: list[BillContext] = []
    cuts: list[TextProjection] = []
    llm_count, batch_count = FakeLlm.count_input_tokens, FakeBatchBackend.count_input_tokens

    def count_llm(backend: FakeLlm, ctx: BillContext) -> int | None:
        counted.append(ctx)
        return llm_count(backend, ctx)

    def count_batch(backend: FakeBatchBackend, ctx: BillContext) -> int | None:
        counted.append(ctx)
        return batch_count(backend, ctx)

    def trace(
        source: str,
        spans: Sequence[tuple[int, int]],
        *,
        head_chars: int = 3000,
        window: int = 1200,
        max_chars: int = 24000,
        fill_head: bool = False,
    ) -> TextProjection:
        result = excerpt_projection(
            source,
            spans,
            head_chars=head_chars,
            window=window,
            max_chars=max_chars,
            fill_head=fill_head,
        )
        cuts.append(result)
        return result

    world = World(
        extractor=FakeTextExtractor(text, page_count=0),
        text_budget_chars=1_500_000,
        batch_only=batch,
        max_bill_cost_usd=0.30,
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    observation = (
        patch.object(analysis_module, "excerpt_projection", trace) if mapped else nullcontext()
    )
    with (
        patch.object(FakeLlm, "count_input_tokens", count_llm),
        patch.object(FakeBatchBackend, "count_input_tokens", count_batch),
        observation,
    ):
        report = world.run(track=False)
    record = world.bill("3039").analysis
    core: dict[str, Any] = {
        "status": world.bill("3039").status,
        "errors": report.model_dump(mode="json")["errors"],
        "analysis_failures": report.analysis_failures,
        "analysis_skipped_cost": report.analysis_skipped_cost,
        "counted": [digest(ctx.model_dump_json().encode()) for ctx in counted],
        "record_sha256": digest(record.model_dump_json(exclude={"selection"}).encode())
        if record
        else None,
        "requests": [],
    }
    ctx = world.llm.contexts[-1] if world.llm.contexts else None
    selection = record.selection if record else None
    for request in world.batch.submitted:
        core["requests"].append(
            {
                "id": request.custom_id,
                "payload_sha256": digest((request.payload_json or "").encode()),
            }
        )
        if not isinstance(request.ctx, BillContext):
            raise ValueError("Runtime replay may submit bill analysis only")
        ctx = request.ctx
    if world.batch.submitted:
        item = world.repo.list_llm_batch_items(world.repo.list_open_llm_batches()[0].batch_id)[0]
        if not isinstance(item.meta, BatchItemMeta):
            raise ValueError("Runtime replay requires analysis metadata")
        selection = item.meta.selection
        core["meta_sha256"] = digest(item.meta.model_dump_json(exclude={"selection"}).encode())
    coverage = [span.model_dump(mode="json") for span in selection.coverage] if selection else []
    checked = None
    if mapped and ctx is not None and selection is not None and counted:
        trimmed = trim_print(text)
        initial = TextBudget(1_500_000).apply(
            trimmed.text, world.container.prefilter.spans(trimmed.text)
        )
        if (
            trimmed.projection is None
            or initial.projection is None
            or initial.text != counted[0].text
        ):
            raise ValueError("Runtime fitting must start from the traced initial budget")
        projection = initial.projection.compose(trimmed.projection)
        for cut in cuts:
            projection = cut.compose(projection)
        if projection.text != ctx.text:
            raise ValueError("Fitted context differs from the observed transform chain")
        checked = check_projection(projection, text)
        expected = [
            (segment.source_start, segment.source_start + segment.end - segment.start)
            for segment in projection.segments
            if segment.source_start is not None
        ]
        actual = [(span.start, span.end) for span in selection.coverage if span.stage == "analysis"]
        if expected != actual:
            raise ValueError("Persisted input ranges differ from the observed transform chain")
        checked["cuts"] = len(cuts)
    return {
        "core": core,
        "coverage": coverage,
        "mapping": checked,
        "selection_json_bytes": len(selection.model_dump_json().encode()) if selection else 0,
    }


def run(
    corpus: Path,
    inventory: Path,
    inventory_sha256: str,
    output: Path,
    *,
    mapped: bool,
    reference: Path | None = None,
    reference_sha256: str | None = None,
) -> int:
    root, target = corpus.resolve(strict=True), output.resolve()
    if output.is_symlink() or target.exists() or target.is_relative_to(root):
        raise ValueError("Output must be new and outside the corpus")
    if inventory.name == ".env" or inventory.resolve().name == ".env":
        raise ValueError("A credential file is not a replay inventory")
    raw = read_artifact(inventory)
    if digest(raw) != inventory_sha256:
        raise ValueError("A pinned replay inventory is required")
    population = json.loads(raw)
    if population.get("suite") != "input-coverage-v2" or population.get("status") != "passed":
        raise ValueError("Inventory must be an accepted full input replay")
    previous = None
    if reference is not None:
        if not mapped or reference.name == ".env" or reference_sha256 is None:
            raise ValueError("Compare requires mapped mode and a pinned baseline")
        raw_reference = read_artifact(reference)
        if digest(raw_reference) != reference_sha256:
            raise ValueError("Baseline SHA mismatch")
        previous = json.loads(raw_reference)
        if (
            previous.get("suite") != "runtime-cost-v1"
            or previous.get("status") != "recorded"
            or previous.get("inventory_sha256") != inventory_sha256
        ):
            raise ValueError("Baseline must declare the same complete population")
    elif reference_sha256 is not None:
        raise ValueError("Reference SHA requires its file")
    before = source_fingerprints()
    result: dict[str, Any] = {
        "suite": "runtime-cost-v1",
        "code_sha": code_version(),
        "source_sha256": before,
        "inventory_sha256": inventory_sha256,
        "observations": {},
        "errors": {},
        "core_changes": [],
        "coverage_changes": [],
        "exclusions": [
            "One text-only fake part per selected raw text; scans and bundle policy are excluded.",
            "Cost cases need a bill heading and 40000 chars; all raw decisions are checked.",
            "Fake token counters and fake model answers, no provider tokenization or legal oracle.",
        ],
    }
    reads = CorpusReads(root)
    reader = DocumentTextExtractor(
        PypdfTextExtractor(),
        DocxTextExtractor(),
        DocTextExtractor(),
        max_member_bytes=32_000_000,
        max_part_chars=400_000,
    )
    with patch.dict(os.environ, {}, clear=True), reads.guard():
        for name, row in sorted(population["observations"].items()):
            try:
                path = (root / name).resolve()
                if not path.is_relative_to(root):
                    raise ValueError("Raw input escapes corpus")
                data = path.read_bytes()
                if digest(data) != row["core"]["input_sha256"]:
                    raise ValueError("Raw input SHA mismatch")
                extracted = reader.extract_document(data)
                prepared = trim_print(extracted.text)
                core = {
                    "input_sha256": digest(data),
                    "extracted_sha256": digest(extracted.text.encode()),
                    "selection_sha256": digest(extracted.selection.model_dump_json().encode()),
                    "trim_sha256": digest(prepared.text.encode()),
                }
                if (
                    any(
                        core[key] != row["core"][key]
                        for key in ("input_sha256", "extracted_sha256", "selection_sha256")
                    )
                    or core["trim_sha256"] != row["core"]["inputs"]["trim"]
                ):
                    raise ValueError(
                        "Raw extraction or selected text changed from the accepted inventory"
                    )
                cases = {}
                eligible = document_kind(extracted.text) == "bill" and len(prepared.text) >= 40000
                if eligible:
                    for batch in (False, True):
                        cases["batch" if batch else "sync"] = runtime_snapshot(
                            extracted.text, batch=batch, mapped=mapped
                        )
                result["observations"][name] = {"core": core, "eligible": eligible, "cases": cases}
            except Exception as exc:
                result["errors"][name] = f"{type(exc).__name__}: {exc}"
    result["changed_inputs"] = [
        name
        for name, sha in reads.initial_sha256.items()
        if not (root / name).is_file() or digest((root / name).read_bytes()) != sha
    ]
    result["changed_code"] = before != source_fingerprints()
    if previous is not None:
        for name in previous["observations"].keys() | result["observations"].keys():
            old, new = previous["observations"].get(name), result["observations"].get(name)
            if (
                old is None
                or new is None
                or old["core"] != new["core"]
                or old["eligible"] != new["eligible"]
                or old["cases"].keys() != new["cases"].keys()
                or any(
                    old["cases"][key]["core"] != new["cases"][key]["core"] for key in old["cases"]
                )
            ):
                result["core_changes"].append(name)
            elif any(
                old["cases"][key]["coverage"] != new["cases"][key]["coverage"]
                for key in old["cases"]
            ):
                result["coverage_changes"].append(name)
    failed = bool(
        result["errors"]
        or result["core_changes"]
        or result["changed_inputs"]
        or result["changed_code"]
    )
    result["status"] = "failed" if failed else "passed" if previous else "recorded"
    with target.open("x") as stream:
        json.dump(result, stream, sort_keys=True, ensure_ascii=False, indent=2)
        stream.write("\n")
    return int(failed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mapped", action="store_true")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--reference-sha256")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    raise SystemExit(
        run(
            args.corpus,
            args.inventory,
            args.inventory_sha256,
            args.output,
            mapped=args.mapped,
            reference=args.reference,
            reference_sha256=args.reference_sha256,
        )
    )


if __name__ == "__main__":
    main()

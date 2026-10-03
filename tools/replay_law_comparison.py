"""Compare production decisions with a saved probe, retaining every changed decision's evidence."""

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tests.fakes import FakeTextExtractor
from tests.harness import World
from tests.scenario.test_law_fingerprint import _analysed_print_then_a_new_version

from lexinform.law_diff import diff_laws
from lexinform.law_digest import prepare_law
from lexinform.sections import law_body
from tools.probe_law_comparison import decide, load, pairs
from tools.probe_law_mutations import mutations


def pipeline_probes(corpus: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for druk, kind in (
        ("810", "standalone_number"),
        ("810", "decimal"),
        ("342", "negation"),
        ("1863", "deadline"),
    ):
        body = law_body(load(corpus, f"sejm/term10/text/{druk}.txt.gz"), preserve_numbers=True)
        assert body is not None
        mutation = next(item for item in mutations(body) if item.kind == kind)
        w = World(
            extractor=FakeTextExtractor(
                by_content={b"%PDF-druk": body, b"%PDF-new": mutation.text},
                page_count=1,
            )
        )
        _analysed_print_then_a_new_version(w, b"%PDF-new")
        report = w.run()
        results.append(
            {
                "druk": druk,
                "kind": kind,
                "old": mutation.old,
                "new": mutation.new,
                "context": mutation.context,
                "reanalyzed": report.reanalyzed,
                "reviews": len(w.llm.change_contexts),
                "errors": report.errors,
            }
        )
        assert report.reanalyzed == 1 and not report.errors, results[-1]
    return results


def run(corpus: Path, reference: Path) -> dict[str, Any]:
    saved = json.loads(reference.read_text())
    previous = {(row["kind"], row["key"], row["druk"]): row for row in saved["pairs"]}
    counts: Counter[str] = Counter()
    changed: list[dict[str, Any]] = []
    inputs: dict[str, str] = {}
    automatic = {"digest", "noise"}
    for index, pair in enumerate(pairs(corpus), 1):
        old, new = load(corpus, pair.old_path), load(corpus, pair.new_path)
        for path, text in ((pair.old_path, old), (pair.new_path, new)):
            inputs[path] = hashlib.sha256(text.encode()).hexdigest()
            assert inputs[path] == saved["inputs"][path], path
        baseline = previous[(pair.kind, pair.key, pair.druk)]["baseline"]
        decision = decide(old, new)
        counts[decision.route] += 1
        if baseline["route"] != decision.route:
            diff = diff_laws(old, new)
            prepared = [value for text in (old, new) if (value := prepare_law(text)) is not None]
            changed.append(
                {
                    **asdict(pair),
                    "before": baseline,
                    "after": asdict(decision),
                    "new_match": baseline["route"] not in automatic and decision.route in automatic,
                    "lost_match": baseline["route"] in automatic
                    and decision.route not in automatic,
                    "evidence": [asdict(proof) for proof in diff.evidence] if diff else [],
                    "hunks": [asdict(hunk) for hunk in diff.hunks] if diff else [],
                    "body_lengths": [len(value.text) for value in prepared],
                }
            )
        if index % 100 == 0:
            print(f"Replayed {index} pairs", flush=True)
    return {
        "reference_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
        "counts": counts,
        "inputs": inputs,
        "changed": changed,
        "new_matches": sum(row["new_match"] for row in changed),
        "lost_matches": sum(row["lost_match"] for row in changed),
        "pipeline": pipeline_probes(corpus),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(args.corpus.resolve()):
        parser.error("Output must be a new file outside the corpus")
    result = run(args.corpus, args.reference)
    with args.output.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {key: result[key] for key in ("counts", "new_matches", "lost_matches", "pipeline")},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

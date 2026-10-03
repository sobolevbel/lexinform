"""Measure missed controlled mutations; citation and layout probes still need human review."""

import argparse
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lexinform.law_digest import law_digest
from lexinform.sections import law_body
from lexinform.services.analysis import text_digest
from tools.probe_law_comparison import guarded_signature, load, pairs

PATTERNS = {
    "deadline": re.compile(r"\b(\d{1,3})(?=\s+dni\b)"),
    "year": re.compile(r"\b(20\d{2})(?=\s+r\.)"),
    "negation": re.compile(r"\b(nie)(?=\s+(?:może|stosuje|jest|przysługuje|podlega)\b)"),
    "decimal": re.compile(r"\b(\d{1,3},\d{1,4})\b"),
    "standalone_number": re.compile(r"^[ \t]*(\d{1,4})[ \t]*$", re.MULTILINE),
    "reference": re.compile(r"\bart\.\s*(\d+)(?=[a-z]*\b)"),
    "scope": re.compile(r"\b(cudzoziemców)\b"),
}


@dataclass(frozen=True)
class Mutation:
    kind: str
    old: str
    new: str
    context: str
    text: str


def mutations(body: str) -> list[Mutation]:
    result: list[Mutation] = []
    for kind, pattern in PATTERNS.items():
        matches = list(pattern.finditer(body))
        if not matches:
            continue
        match = matches[-1] if kind in {"deadline", "year"} else matches[0]
        old = match[1]
        if kind == "negation":
            new = ""
        elif kind == "decimal":
            new = old.replace(",", "")
        elif kind == "scope":
            new = "obywateli polskich"
        else:
            new = str(int(old) + 1)
        result.append(
            Mutation(
                kind,
                old,
                new,
                body[max(0, match.start() - 90) : match.end() + 90],
                body[: match.start(1)] + new + body[match.end(1) :],
            )
        )
    deadlines = list(PATTERNS["deadline"].finditer(body))
    if len(deadlines) > 1:
        first = deadlines[0]
        other = next((match for match in deadlines[1:] if match[1] != first[1]), None)
        if other is not None:
            changed = body[: other.start(1)] + first[1] + body[other.end(1) :]
            changed = changed[: first.start(1)] + other[1] + changed[first.end(1) :]
            result.append(
                Mutation(
                    "deadline_swap",
                    f"{first[1]} / {other[1]}",
                    f"{other[1]} / {first[1]}",
                    body[first.start() : first.end() + 90],
                    changed,
                )
            )
    return result


def run(corpus: Path) -> dict[str, Any]:
    paths = sorted({path for pair in pairs(corpus) for path in (pair.old_path, pair.new_path)})
    counts: dict[str, Counter[str]] = {kind: Counter() for kind in (*PATTERNS, "deadline_swap")}
    missed: list[dict[str, Any]] = []
    for index, path in enumerate(paths, 1):
        body = law_body(load(corpus, path))
        if body is None:
            continue
        original, guarded = law_digest(body), guarded_signature(body)
        original_text = text_digest(body)
        for mutation in mutations(body):
            old_missed = original is not None and original == law_digest(mutation.text)
            text_missed = original_text == text_digest(mutation.text)
            guard_missed = guarded is not None and guarded == guarded_signature(mutation.text)
            counts[mutation.kind].update(
                {
                    "total": 1,
                    "digest_missed": int(old_missed),
                    "text_digest_missed": int(text_missed),
                    "guard_missed": int(guard_missed),
                }
            )
            if old_missed or text_missed or guard_missed:
                missed.append(
                    {
                        **{k: v for k, v in asdict(mutation).items() if k != "text"},
                        "path": path,
                        "digest_missed": old_missed,
                        "text_digest_missed": text_missed,
                        "guard_missed": guard_missed,
                    }
                )
        if index % 100 == 0:
            print(f"Mutated {index}/{len(paths)} documents", flush=True)
    return {"documents": len(paths), "counts": counts, "missed": missed}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(args.corpus.resolve()):
        parser.error("Output must be a new file outside the corpus")
    result = run(args.corpus)
    with args.output.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result["counts"], indent=2))


if __name__ == "__main__":
    main()

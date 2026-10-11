"""Replay publication titles from the read-only corpus into a separate audit file."""

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lexinform.publication_titles import publication_title


@dataclass(frozen=True)
class TitleRow:
    source: str
    path: str
    key: str
    field: str
    title: str


def records(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        data = data.get("entries", [data])
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise ValueError(f"Unexpected records in {path}")
    return data


def corpus_titles(root: Path) -> list[TitleRow]:
    inputs: list[tuple[str, Path, tuple[str, ...]]] = []
    for term in (8, 9, 10):
        base = root / ("sejm/snapshots/2026-10-03/term10" if term == 10 else f"sejm/term{term}")
        inputs.extend(
            [
                (f"Sejm {term}", base / "processes.json", ("title", "titleFinal")),
                (f"Bills {term}", base / "bills.json", ("title",)),
            ]
        )
    inputs.extend(
        ("RCL", path, ("title",)) for path in sorted((root / "corpus").glob("[0-9]*.json"))
    )
    inputs.append(("Wykaz", root / "wykaz/parsed-2026-09-13.json", ("title",)))
    inputs.extend(("ELI", path, ("title",)) for path in sorted((root / "sejm/eli").glob("*.json")))
    rows: list[TitleRow] = []
    for source, path, fields in inputs:
        for index, record in enumerate(records(path)):
            key = str(record.get("number", record.get("id", index)))
            for field in fields:
                title = record.get(field)
                if isinstance(title, str) and title:
                    rows.append(TitleRow(source, str(path.relative_to(root)), key, field, title))
    return rows


def audit(rows: list[TitleRow]) -> dict[str, Any]:
    unique = {row.title for row in rows}
    changed = {title: publication_title(title) for title in sorted(unique)}
    changed = {old: new for old, new in changed.items() if old != new}
    prefixes: Counter[str] = Counter()
    failures: list[str] = []
    mutation_checks = 0
    mutations = (
        " · data: 01.10.2026 zamiast 02.10.2026; termin: 30 dni zamiast 31 dni",
        " · kwota: 92,67 zł zamiast 9267 zł",
        " · nie dotyczy cudzoziemców",
        " · tylko osoby z pobytem stałym",
        " · art. 12 ust. 3 zamiast art. 13 ust. 2",
    )
    for old, new in changed.items():
        normalized = " ".join(old.split())
        suffix = normalized[-len(new) :]
        if not new or suffix[0].upper() + suffix[1:] != new:
            failures.append(f"Changed subject: {old}")
        prefixes[normalized[: -len(new)]] += 1
        for mutation in mutations:
            mutation_checks += 1
            if publication_title(old + mutation) != new + mutation:
                failures.append(f"Changed mutation: {old}{mutation}")
    for title in unique:
        short = publication_title(title)
        if publication_title(short) != short:
            failures.append(f"Not idempotent: {title}")
    return {
        "rows": len(rows),
        "unique_titles": len(unique),
        "changed_unique_titles": len(changed),
        "changed_rows": sum(row.title in changed for row in rows),
        "sources": dict(Counter(row.source for row in rows)),
        "changed_sources": dict(Counter(row.source for row in rows if row.title in changed)),
        "removed_prefixes": dict(prefixes.most_common()),
        "mutation_checks": mutation_checks,
        "failures": failures,
        "titles": [
            {**asdict(row), "publication_title": publication_title(row.title)} for row in rows
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = args.corpus.resolve()
    output = args.output.resolve()
    if output.is_relative_to(root):
        parser.error("Output must be outside the read-only corpus")
    report = audit(corpus_titles(root))
    with output.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    summary = {
        key: value for key, value in report.items() if key not in {"titles", "removed_prefixes"}
    }
    summary["most_common_prefixes"] = dict(list(report["removed_prefixes"].items())[:20])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if report["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

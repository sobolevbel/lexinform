"""Offline hypotheses for material text changes; never used by the production comparator."""

import argparse
import gzip
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lexinform.law_diff import diff_laws
from lexinform.law_digest import law_digest, readable_law
from lexinform.models import RclProject
from lexinform.sections import law_body, trim_print
from lexinform.services.analysis import text_digest

DATE = re.compile(
    r"\b(W\s+ustawie)\s+(?:z\s+)?dnia(?=\s+\d{1,2}\s+"
    r"(?:stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|września|"
    r"października|listopada|grudnia)\s+\d{4}\s+r\.)"
)
BLANK = re.compile(
    r"(Załączniki?\s+do\s+ustawy\s+z\s+dnia)[\s.…]*(?:r\.)?"
    r"(?=\s*(?:\(\s*Dz\.|Załącznik\s+nr))"
)
STAMP_HEADING = r"^\s*Opracowano pod względem prawnym,\s*legislacyjnym i redakcyjnym"
STAMP = re.compile(
    STAMP_HEADING + r"[^\d]{0,400}\Z",
    re.MULTILINE | re.IGNORECASE,
)
FOOTNOTE = re.compile(
    r"^\s*\d{1,2}\)\s*Zmiany\s+(?:tekstu\s+jednolitego\s+)?(?:wymienionej\s+)?"
    r"ustawy\s+(?:zostały\s+)?ogłoszon[eo]\s+w\s+"
    r"(?:Dz\.|U\.|z|r\.|poz\.|i|oraz|[\d\s,–-])+\.(?=\s*(?:\n|\Z))",
    re.MULTILINE | re.IGNORECASE,
)
FEATURES = frozenset({"date", "blank", "stamp", "footnote"})
TOKEN = re.compile(r"[^\W\d_]+|[+-]?\d+(?:[,.:/-]\d+)*|[<>≤≥=+/%]")
NUMBER = re.compile(r"[+-]?\d+(?:,\d+)?")
NAME = re.compile(r"[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+(?:[- ][A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+){1,3}")
ROLE_WORDS = frozenset(
    [
        "zastępca",
        "dyrektora",
        "dyrektor",
        "departamentu",
        "departament",
        "legislacyjnego",
        "legislacyjny",
        "prawnego",
        "ustroju",
        "sądów",
        "ministerstwa",
        "ministerstwie",
        "sprawiedliwości",
        "edukacji",
        "nauki",
        "rozwoju",
        "technologii",
        "w",
        "i",
    ]
)


@dataclass(frozen=True)
class Pair:
    kind: str
    key: str
    druk: str
    old_path: str
    new_path: str
    historical: str


@dataclass(frozen=True)
class Decision:
    route: str
    ratio: float = 0
    chars: int = 0


def decide(old: str, new: str, *, hashes: bool = True) -> Decision:
    if hashes and (
        text_digest(old) == text_digest(new)
        or ((digest := law_digest(old)) is not None and digest == law_digest(new))
    ):
        return Decision("digest")
    diff = diff_laws(old, new)
    if diff is None:
        return Decision("unreadable", 1)
    route = "noise" if not diff.hunks else "review"
    if diff.hunks and (diff.ratio > 0.15 or diff.chars > 60_000):
        route = "full"
    return Decision(route, diff.ratio, diff.chars)


def technical(text: str, features: frozenset[str] = FEATURES) -> str:
    body = law_body(text)
    if body is None:
        return text
    if "footnote" in features:
        body = FOOTNOTE.sub("", body)
    if "date" in features:
        body = DATE.sub(r"\1 z dnia", body)
    if "blank" in features:
        body = BLANK.sub(r"\1", body)
    if "stamp" in features and (match := STAMP.search(body)) and signed_stamp(match.group()):
        body = body[: match.start()]
    return body


def signed_stamp(block: str) -> bool:
    lines = re.sub(STAMP_HEADING, "", block, count=1, flags=re.IGNORECASE).splitlines()
    roles = names = 0
    for line in lines:
        line = " ".join(line.split()).strip()
        if not line or line.strip("/()- ").lower() == "podpisano elektronicznie":
            continue
        if set(line.lower().split()) <= ROLE_WORDS:
            roles += 1
        elif NAME.fullmatch(line):
            names += 1
        else:
            return False
    return roles > 0 and names == 1


def guarded_signature(text: str) -> str | None:
    body = law_body(text)
    if body is None:
        return None
    prepared = re.sub(r"\b(rok|roku)\s+(20\d)\s+(\d)\b", r"\1 \2\3", technical(body))
    protected = re.sub(r"(?m)^([ \t]*)(\d+)([ \t]*)$", r"\1⟦\2⟧\3", prepared)
    readable = readable_law(protected)
    if readable is None:
        return None
    letters = re.sub(r"[\W_]+", "", readable).lower()
    numbers = re.findall(r"[+-]?\d+(?:[,.:/-]\d+)*|[<>≤≥=+/%]", readable)
    return letters + "\x1f" + json.dumps(numbers)


def indexed_tokens(text: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    positions: list[int] = []
    for match in TOKEN.finditer(text.translate(str.maketrans("–−", "--"))):
        token = match.group().lower()
        if any(char.isdigit() for char in token):
            token = "#" + token + "#"
        normalized.append(token)
        positions.extend([match.start()] * (len(token) - 1) + [match.end() - 1])
    return "".join(normalized), positions


def align_table(old: str, new: str) -> tuple[str, int, int]:
    target, offsets = indexed_tokens(new)
    end = 0
    edits: list[tuple[int, int, str]] = []
    rows = 0
    for line in old.splitlines():
        cells = line.split("\t")
        if len(cells) < 4 or not NUMBER.fullmatch(cells[-1].strip()):
            continue
        rows += 1
        body, _ = indexed_tokens(" ".join(cells[:-1]))
        value, _ = indexed_tokens(cells[-1])
        if not body:
            continue
        start = target.find(body[:24], end)
        if start < 0:
            continue
        stop = start + len(body) + len(value)
        candidate = target[start:stop]
        if not any(
            candidate[: m.start()] + candidate[m.end() :] == body
            for m in re.finditer(re.escape(value), candidate)
        ):
            continue
        edits.append((offsets[start], offsets[stop - 1] + 1, line.strip()))
        end = stop
    for start, stop, replacement in reversed(edits):
        new = new[:start] + replacement + new[stop:]
    return new, len(edits), rows


def articles(text: str) -> dict[str, str] | None:
    if law_body(text) is None:
        return None
    body = technical(text)
    markers = list(re.finditer(r"^Art\.\s*(\d+[a-z]*)\.", body, re.MULTILINE))
    if not markers or len({match[1] for match in markers}) != len(markers):
        return None
    result: dict[str, str] = {}
    for index, match in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(body)
        result[match[1]] = indexed_tokens(body[match.end() : end])[0]
    return result


def pairs(corpus: Path) -> list[Pair]:
    entities = json.loads((corpus / "INDEX.json").read_text())["entities"]
    saved = json.loads((corpus / "checks/out/92_transitions.json").read_text())
    result: list[Pair] = []
    for row in saved:
        if row["kind"] == "rpw":
            continue
        entity = entities[row["key"]]
        if row["kind"] == "rcl":
            project = RclProject.model_validate_json((corpus / entity["project"]).read_text())
            selected = project.text_documents().get("bill")
            match = re.search(r"dokument(\d+)", selected.url) if selected else None
            if match is None:
                raise ValueError(f"No bill selected for {row['key']}")
            old_path = f"documents/text/{match[1]}.txt.gz"
        else:
            old_path = entity["text"]
        result.append(
            Pair(
                row["kind"],
                row["key"],
                row["druk"],
                old_path,
                f"sejm/term10/text/{row['druk']}.txt.gz",
                row["decision"],
            )
        )
    return result


def load(corpus: Path, relative: str) -> str:
    with gzip.open(corpus / relative, "rt") as stream:
        return trim_print(stream.read()).text


def run(corpus: Path) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    inputs: dict[str, str] = {}
    for pair in pairs(corpus):
        old, new = load(corpus, pair.old_path), load(corpus, pair.new_path)
        for path, text in ((pair.old_path, old), (pair.new_path, new)):
            inputs[path] = hashlib.sha256(text.encode()).hexdigest()
        baseline = decide(old, new)
        normalized_old, normalized_new = technical(old), technical(new)
        aligned, matched, total = align_table(normalized_old, normalized_new)
        guarded = guarded_signature(old)
        old_articles, new_articles = articles(old), articles(new)
        if baseline.route == "digest":
            candidate = baseline
            table_candidate = baseline
        else:
            candidate = decide(normalized_old, normalized_new, hashes=False)
            table_candidate = (
                decide(normalized_old, aligned, hashes=False) if matched else candidate
            )
        ablations: dict[str, str] = {}
        if candidate.route == "noise" and baseline.route not in {"noise", "digest"}:
            for feature in sorted(FEATURES):
                selected = frozenset({feature})
                ablations[feature] = decide(
                    technical(old, selected), technical(new, selected), hashes=False
                ).route
        results.append(
            {
                **asdict(pair),
                "baseline": asdict(baseline),
                "technical": asdict(candidate),
                "tables": asdict(table_candidate),
                "rows_matched": matched,
                "rows": total,
                "ablations": ablations,
                "guarded_equal": guarded is not None and guarded == guarded_signature(new),
                "guarded_tables_equal": (
                    guarded is not None and guarded == guarded_signature(aligned)
                ),
                "articles_equal": old_articles is not None and old_articles == new_articles,
                "articles_comparable": old_articles is not None and new_articles is not None,
            }
        )
        if len(results) % 50 == 0:
            print(f"Compared {len(results)} pairs", flush=True)
    counts = {
        variant: dict(Counter(row[variant]["route"] for row in results))
        for variant in ("baseline", "technical", "tables")
    }
    counts["proofs"] = dict(
        Counter(
            {
                key: sum(bool(row[key]) for row in results)
                for key in (
                    "guarded_equal",
                    "guarded_tables_equal",
                    "articles_equal",
                    "articles_comparable",
                )
            }
        )
    )
    return {"counts": counts, "inputs": inputs, "pairs": results}


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

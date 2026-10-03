"""Bounded technical equivalences, retaining the text that justifies each replacement."""

import re
from dataclasses import dataclass

from lexinform.sections import law_body

DATE = re.compile(
    r"\b(W\s+ustawie)\s+(?:z\s+)?dnia(?=\s+\d{1,2}\s+"
    r"(?:stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|września|"
    r"października|listopada|grudnia)\s+\d{4}\s+r\.)"
)
BLANK = re.compile(
    r"(Załączniki?\s+do\s+ustawy\s+z\s+dnia)[\s.…]*(?:r\.)?"
    r"(?=\s*(?:\(\s*Dz\.|Załącznik\s+nr))"
)
STAMP_HEADING = (
    r"^\s*(?:Opracowano|Za zgodność) pod względem prawnym,\s*legislacyjnym i redakcyjnym"
)
STAMP = re.compile(STAMP_HEADING + r"[^\d]{0,400}\Z", re.MULTILINE | re.IGNORECASE)
FOOTNOTE = re.compile(
    r"^\s*\d{1,2}\)\s*Zmiany\s+(?:tekstu\s+jednolitego\s+)?(?:wymienionej\s+)?"
    r"ustawy\s+(?:zostały\s+)?ogłoszon[eo]\s+w\s+"
    r"(?:Dz\.|U\.|z|r\.|poz\.|i|oraz|[\d\s,–-])+\.(?=\s*(?:\n|\Z))",
    re.MULTILINE | re.IGNORECASE,
)
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
        "finansów",
        "w",
        "i",
    ]
)
DECORATED_PAGE = re.compile(r"^[ \t]*[–—-][ \t]*\d{1,4}[ \t]*[–—-][ \t]*$", re.MULTILINE)


@dataclass(frozen=True)
class TechnicalEvidence:
    rule: str
    old: str
    new: str


@dataclass(frozen=True)
class TechnicalText:
    text: str
    evidence: tuple[TechnicalEvidence, ...]


def signed_stamp(block: str) -> bool:
    """An RCL stamp carrying only a role and one name, so nothing of the act follows it."""
    lines = re.sub(STAMP_HEADING, "", block, count=1, flags=re.IGNORECASE).splitlines()
    roles = names = 0
    for line in lines:
        line = " ".join(line.split()).strip()
        if not line or line.strip("/()- ").lower() in {
            "podpisano elektronicznie",
            "podpisano kwalifikowanym podpisem elektronicznym",
        }:
            continue
        if set(line.lower().split()) <= ROLE_WORDS:
            roles += 1
        elif NAME.fullmatch(line):
            names += 1
        else:
            return False
    return roles > 0 and names == 1


def technical_law(text: str) -> TechnicalText | None:
    """The act's body with each provably technical fragment removed and recorded."""
    extracted = law_body(text, preserve_numbers=True)
    if extracted is None:
        return None
    body = extracted
    evidence: list[TechnicalEvidence] = []

    def replace(pattern: re.Pattern[str], replacement: str, rule: str) -> None:
        nonlocal body

        def matched(match: re.Match[str]) -> str:
            value = match.expand(replacement)
            if value != match.group():
                evidence.append(TechnicalEvidence(rule, match.group(), value))
            return value

        body = pattern.sub(matched, body)

    replace(DECORATED_PAGE, "", "page_number")
    replace(FOOTNOTE, "", "publication_footnote")
    replace(DATE, r"\1 z dnia", "statute_date_formula")
    replace(BLANK, r"\1", "blank_appendix_date")
    if (match := STAMP.search(body)) and signed_stamp(match.group()):
        evidence.append(TechnicalEvidence("editorial_signature", match.group(), ""))
        body = body[: match.start()]
    replace(re.compile(r"\b(rok|roku)\s+(20\d)\s+(\d)\b"), r"\1 \2\3", "split_year")
    replace(
        re.compile(r"\b((?:art\.|ust\.|pkt)\s+\d+[a-z]*)\s*[-–−]\s*(\d+[a-z]*)\b"),
        r"\1-\2",
        "reference_range_spacing",
    )
    return TechnicalText(body, tuple(evidence))


def indexed_tokens(text: str) -> tuple[str, list[int]]:
    """Tokens joined into one key, numbers fenced by `#`, with each character's source offset."""
    normalized: list[str] = []
    positions: list[int] = []
    for match in TOKEN.finditer(text.translate(str.maketrans("–−", "--"))):
        token = match.group().lower()
        if any(char.isdigit() for char in token):
            token = "#" + token + "#"
        normalized.append(token)
        positions.extend([match.start()] * (len(token) - 1) + [match.end() - 1])
    return "".join(normalized), positions


def align_table(old: str, new: str) -> TechnicalText:
    """`new` with each tab-separated row of `old` restored where its words and value match."""
    target, offsets = indexed_tokens(new)
    end = 0
    edits: list[tuple[int, int, str]] = []
    for line in old.splitlines():
        cells = line.split("\t")
        if len(cells) < 4 or not NUMBER.fullmatch(cells[-1].strip()):
            continue
        body, _ = indexed_tokens(" ".join(cells[:-1]))
        value, _ = indexed_tokens(cells[-1])
        if not body:
            continue
        start = target.find(body[:24], end)
        if start < 0:
            continue
        stop = start + len(body) + len(value)
        candidate = target[start:stop]
        if stop > len(target) or not any(
            candidate[: m.start()] + candidate[m.end() :] == body
            for m in re.finditer(re.escape(value), candidate)
        ):
            continue
        edits.append((offsets[start], offsets[stop - 1] + 1, line.strip()))
        end = stop
    evidence = tuple(
        TechnicalEvidence("table_row", new[start:stop], replacement)
        for start, stop, replacement in edits
        if new[start:stop] != replacement
    )
    for start, stop, replacement in reversed(edits):
        new = new[:start] + replacement + new[stop:]
    return TechnicalText(new, evidence)

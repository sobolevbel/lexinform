import datetime as dt
import hashlib
import re

from lexinform.law_digest import law_digest
from lexinform.sections import trim_print


def normative_annex_key(text: str) -> tuple[int, str]:
    number = re.search(r"załącznik\s+nr\s+(\d+)\b", text[:1200], re.I)
    return (int(number[1]) if number else 0, hashlib.sha256(text.encode()).hexdigest())


_MONTHS = (
    "stycznia",
    "lutego",
    "marca",
    "kwietnia",
    "maja",
    "czerwca",
    "lipca",
    "sierpnia",
    "września",
    "października",
    "listopada",
    "grudnia",
)
_SUBJECT = re.compile(r"^\s*u\s*s\s*t\s*a\s*w\s*a\s*$", re.I | re.M)
_ARTICLE = re.compile(r"\bArt\.\s*1\b", re.I)
_PREFIX = (
    r"(?im)^[ \t]*`?(?:(?:Etap:[^\n(]{1,120}\(\s*)|(?:Wersja\s*\d+(?:[.,]\d+)?\s+))?"
    r"projekt(?:\s+ustawy)?\s*,?\s*(?:z\s+(?:dnia\s+)?)?"
)
_NUMERIC_DRAFT = re.compile(_PREFIX + r"(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4})(?!\d)")
_SPACED_DRAFT = re.compile(_PREFIX + r"(\d{1,2})[ \t]+(\d{1,2})[ \t]+(\d{4})(?!\d)")
_DRAFT = re.compile(_PREFIX + r"(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})(?!\d)")
_UPDATE = re.compile(
    r"[ \t]*(?:r\.)?[ \t]*,[ \t]*aktualizacja(?:[ \t]+z[ \t]+dnia)?[ \t]*:?[ \t]*",
    re.I,
)
_UPDATE_DATES = tuple(
    re.compile(pattern.pattern.removeprefix(_PREFIX), re.I)
    for pattern in (_NUMERIC_DRAFT, _SPACED_DRAFT, _DRAFT)
)


def _dated_match(match: re.Match[str]) -> dt.date | None:
    try:
        month = int(match[2]) if match[2].isdigit() else _MONTHS.index(match[2].casefold()) + 1
        return dt.date(int(match[3]), month, int(match[1]))
    except ValueError:
        return None


def draft_version_evidence(text: str) -> tuple[tuple[dt.date | None, int, int], ...]:
    heading = _SUBJECT.search(text[:1200])
    article = _ARTICLE.search(text[:1200])
    end = min(1200, heading.start() if heading else 1200, article.start() if article else 1200)
    matches: list[tuple[dt.date | None, int, int]] = []
    for pattern in (_NUMERIC_DRAFT, _SPACED_DRAFT, _DRAFT):
        for match in pattern.finditer(text[:end]):
            value = _dated_match(match)
            last = match.end()
            if update := _UPDATE.match(text, last, end):
                updated = next(
                    (
                        found
                        for date in _UPDATE_DATES
                        if (found := date.match(text, update.end(), end))
                    ),
                    None,
                )
                revision = _dated_match(updated) if updated else None
                last = updated.end() if updated else update.end()
                value = (
                    revision
                    if value is not None
                    and revision is not None
                    and revision >= value
                    and not _UPDATE.match(text, last, end)
                    else None
                )
            matches.append((value, match.start(), last))
    return tuple(matches)


def draft_version_span(text: str) -> tuple[dt.date, int, int] | None:
    matches = draft_version_evidence(text)
    if not matches or len({value for value, _, _ in matches}) != 1:
        return None
    value, start, end = matches[0]
    return (value, start, end) if value is not None else None


def draft_version(text: str) -> dt.date | None:
    match = draft_version_span(text)
    return match[0] if match else None


def bill_subject(text: str) -> str:
    heading = _SUBJECT.search(text[:1200])
    if heading is None:
        return ""
    body = text[heading.end() :]
    article = _ARTICLE.search(body)
    if article is None:
        return ""
    title = body[: article.start()]
    title = re.split(
        r"(?im)^[ \t]*Rozdział[ \t]+(?:\d+|[IVXLCDM]+)[ \t]*(?:\n|$)", title, maxsplit=1
    )[0]
    subject = re.search(r"\b(?:o\s+|Prawo\s+|Kodeks\s+).+", title, re.S)
    if subject is None:
        return ""
    return re.sub(r"[\W_]+", "", subject[0]).casefold()


def shared_amended_act(texts: list[str]) -> bool:
    references: list[str] = []
    for text in texts:
        match = re.search(r"\bW\s+ustawie\s+z\s+dnia\s+([^()]{10,400})\(", text, re.I)
        if match is None:
            return False
        references.append(re.sub(r"[\W_]+", "", match[1]).casefold())
    return len(set(references)) == 1


def normative_name(name: str) -> bool:
    return bool(re.search(r"za[łl][aą]cznik.*do (?:niniejszej )?ustawy", name, re.I))


def join_normative_parts(text: str, parts: list[str]) -> str:
    if not parts:
        return text
    tail = re.search(r"(?im)^\s*(?:UZASADNIENIE|Nazwa projektu|OCENA SKUTKÓW REGULACJI)\s*$", text)
    position = tail.start() if tail is not None else len(text)
    return text[:position] + "\f" + "\f".join(parts) + "\f" + text[position:]


def regulation_support(text: str, *, name: str = "") -> bool:
    return bool(
        re.search(r"\brozp(?:[.\s_]|orz[aą]dze)", name.replace("_", " "), re.I)
        or re.search(
            r"(?:^|\n)(?:UZASADNIENIE\s*|Nazwa projektu\s*)?Projekt\s+rozporz[aą]dzenia",
            text[:400],
            re.I,
        )
    )


def comparable_versions(texts: list[str]) -> tuple[int, ...]:
    if not texts:
        return ()
    subjects = {bill_subject(text) for text in texts}
    dates = [draft_version(text) for text in texts]
    same_subject = len(subjects) == 1 and "" not in subjects
    indices = tuple(range(len(texts)))
    if (same_subject or shared_amended_act(texts)) and all(dates):
        newest = max(date for date in dates if date is not None)
        indices = tuple(index for index in indices if dates[index] == newest)
    identities = {law_digest(trim_print(texts[i]).text) or texts[i] for i in indices}
    return indices if len(identities) == 1 else ()

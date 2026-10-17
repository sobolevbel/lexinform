import datetime as dt
import re

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
_DRAFT = re.compile(
    r"projekt\s+z\s+(?:dnia\s+)?(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})",
    re.I,
)
_SUBJECT = re.compile(r"^\s*u\s*s\s*t\s*a\s*w\s*a\s*$", re.I | re.M)
_ARTICLE = re.compile(r"\bArt\.\s*1\b", re.I)
_NUMERIC_DRAFT = re.compile(r"projekt\s+z\s+(?:dnia\s+)?(\d{1,2})[./-](\d{1,2})[./-](\d{4})", re.I)


def draft_version(text: str) -> dt.date | None:
    numeric = _NUMERIC_DRAFT.search(text[:1200])
    if numeric is not None:
        try:
            return dt.date(int(numeric[3]), int(numeric[2]), int(numeric[1]))
        except ValueError:
            return None
    match = _DRAFT.search(text[:1200])
    if match is None:
        return None
    try:
        return dt.date(int(match[3]), _MONTHS.index(match[2].casefold()) + 1, int(match[1]))
    except ValueError:
        return None


def bill_subject(text: str) -> str:
    heading = _SUBJECT.search(text[:1200])
    if heading is None:
        return ""
    body = text[heading.end() :]
    article = _ARTICLE.search(body)
    if article is None:
        return ""
    title = body[: article.start()]
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

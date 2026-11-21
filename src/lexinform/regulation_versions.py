"""Compare regulation editions without treating typography as a competing draft."""

import datetime as dt
import re
from hashlib import sha256

from lexinform.document_versions import VersionComparison, draft_version, version_date
from lexinform.law_technical import DECORATED_PAGE

_HEADING = re.compile(r"(?im)^[ \t]*r\s*o\s*z\s*p\s*o\s*r\s*z\s*ą\s*d\s*z\s*e\s*n\s*i\s*e\s*$")
_TITLE = re.compile(
    r"\A(?P<issuer>.*?)^[ \t]*z\s+dnia(?P<date>[^\n]*)\n"
    r"(?P<subject>.*?)\bNa\s+podstawie\b",
    re.S | re.I | re.M,
)
_DRAFT_MARKER = re.compile(r"\bp\s*r\s*o\s*j\s*e\s*k\s*t\b|\b(?:wersja|etap)\b", re.I)
_NOTE = re.compile(r"\[przypis ([^:\]\s]+): (.*?)\]", re.S)
_NOTE_START = re.compile(r"^\s*(?:\d{1,2})?\)\s*")
_CHARS = str.maketrans({"–": "-", "—": "-", "‑": "-", "−": "-", "\u00ad": "", "\u00a0": " "})
type TextKey = tuple[str, tuple[str, ...]]


def _layout(text: str) -> str:
    text = text.translate(_CHARS).replace("\f", "\n")
    text = DECORATED_PAGE.sub("", text)
    text = re.sub(r"([^\W\d_])-\s*\n\s*([^\W\d_])", r"\1\2", text)
    return re.sub(r"\b(z\s+)(20\d)[ \t]+(\d)(?=\s+r\.)", r"\1\2\3", text)


def _key(text: str) -> TextKey:
    text = _layout(text)
    return re.sub(r"[\W_]+", "", text).casefold(), tuple(
        re.findall(r"[+-]?\d+(?:[,.:/-]\d+)*|[<>≤≥=+/%]", text)
    )


def regulation_subject(text: str) -> str:
    text = _NOTE.sub("", text)
    heading = _HEADING.search(text[:1200])
    if heading is None:
        return ""
    title = _TITLE.match(text[heading.end() : 6000])
    if title is None:
        return ""
    issuer = re.sub(r"\d{1,2}\)", "", title["issuer"])
    return _key(issuer + title["subject"])[0]


def regulation_version(text: str) -> dt.date | None:
    text = _NOTE.sub("", text)
    heading = _HEADING.search(text[:1200])
    if heading is None:
        return None
    prefix = text[: heading.start()]
    if date := draft_version(prefix):
        return date
    if _DRAFT_MARKER.search(prefix):
        return None
    title = _TITLE.match(text[heading.end() : 6000])
    if (
        title is None
        or not title["issuer"].strip()
        or not title["subject"].strip()
        or _DRAFT_MARKER.search(title["issuer"])
        or re.search(r"\bNa\s+podstawie\b|§|\bArt\.\s*\d", title["issuer"], re.I)
    ):
        return None
    return version_date(title["date"])


def _identity(text: str, definitions: dict[str, str]) -> tuple[TextKey, tuple[TextKey, ...]]:
    notes: list[TextKey] = []
    markers: dict[str, str] = {}

    def reference(key: TextKey) -> str:
        return "footnote" + sha256(repr(key).encode()).hexdigest()

    def word_note(match: re.Match[str]) -> str:
        key = _key(_NOTE_START.sub("", match[2]).strip())
        notes.append(key)
        return reference(key)

    text = _NOTE.sub(word_note, _layout(text))
    if not notes:
        for chars, content in definitions.items():
            pattern = re.compile(
                r"(?m)^[ \t]*(\d{1,2})\)[ \t]*" + r"[\W_]*".join(map(re.escape, chars)), re.I
            )

            def pdf_note(match: re.Match[str], expected: TextKey = _key(content)) -> str:
                found = re.sub(r"^\s*\d{1,2}\)\s*", "", match[0])
                if _key(found) != expected:
                    return match[0]
                markers[match[1]] = reference(expected)
                notes.append(expected)
                return ""

            text = pattern.sub(pdf_note, text)
    for marker, replacement in markers.items():
        text = re.sub(r"(?<=[^\W\d_]|\.)" + re.escape(marker) + r"\)", replacement, text)
        paragraph = re.search(r"(?m)^\s*§\s*1\.", text)
        if paragraph:
            text = (
                re.sub(
                    r"(?<=[^\W\d_])[ \t]+" + re.escape(marker) + r"\)",
                    replacement,
                    text[: paragraph.start()],
                )
                + text[paragraph.start() :]
            )
    return _key(text), tuple(sorted(notes))


def compare_regulation_versions(
    texts: list[str], *, prefer_newer: bool = True
) -> VersionComparison:
    indices = tuple(range(len(texts)))
    if not texts:
        return VersionComparison((), ())
    subjects = {regulation_subject(text) for text in texts}
    dates = [regulation_version(text) for text in texts]
    date_filter = None
    if prefer_newer and len(subjects) == 1 and "" not in subjects and all(dates):
        known = tuple(date for date in dates if date is not None)
        indices = tuple(i for i in indices if known[i] == max(known))
        if len(indices) < len(texts):
            date_filter = known
    definitions = {
        _key(content)[0]: content
        for i in indices
        for match in _NOTE.finditer(texts[i])
        if len(content := _NOTE_START.sub("", match[2]).strip()) >= 30
    }
    identities = {_identity(texts[i], definitions) for i in indices}
    return VersionComparison(indices, indices if len(identities) == 1 else (), date_filter)

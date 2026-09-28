"""Whether two texts carry the same act: 53 of 446 RCL-to-druk bodies match here, 6 as text."""

import hashlib
import re

from lexinform.sections import law_body

_CHARACTERS = str.maketrans(
    {
        "„": '"',
        "”": '"',
        "“": '"',
        "«": '"',
        "»": '"',
        "’": "'",
        "‘": "'",
        "–": "-",
        "—": "-",
        "‑": "-",
        "−": "-",
        "‒": "-",
        "­": "",
        " ": " ",
    }
)
_STAMP = re.compile(r"za\s+zgodność\s+pod\s+względem\s+prawnym[\s\S]{0,300}?(?=\n\s*\n|\Z)", re.I)
_FOOTNOTE_BODY = re.compile(
    r"^\s*\d{1,2}\)\s*(?:Niniejsz\w+\s+ustaw\w+|Zmiany\s+tekstu\s+jednolitego|Zmiany\s+wymienion\w+"
    r"|Ustawa\s+niniejsza|Przepisy\s+niniejszej)[\s\S]*?(?=\n\s*\n|\n\s*(?:Art\.|\d+\)|[a-z]\))|\Z)",
    re.MULTILINE | re.IGNORECASE,
)
_CITATION = re.compile(r"\(\s*Dz\.\s*U\.[^()]*(?:\([^()]*\)[^()]*)*\)", re.IGNORECASE)
_LIST_MARK = re.compile(r"(?:(?<=^)|(?<=[\s,;:]))(?:[a-z]{1,2}|\d{1,3}[a-z]?)\)", re.MULTILINE)
_PAGE_NUMBER = re.compile(r"^\s*-?\s*\d{1,4}\s*-?\s*$", re.MULTILINE)
_HYPHEN_BREAK = re.compile(r"(\w)-\s*\n\s*(\w)")
_FOOTNOTE_REF = re.compile(r"(?<=[^\W\d_])\d{1,2}\)")
_NOT_LETTER_OR_DIGIT = re.compile(r"[\W_]+")
MIN_LAW_CHARS = 200
"""A body shorter than this, normalised, is a fragment and says nothing about the act."""


def normalised_law(text: str) -> str | None:
    """The act of `text` as letters and digits alone, its legal-technical apparatus dropped."""
    body = law_body(text)
    if body is None:
        return None
    body = body.translate(_CHARACTERS).replace(",,", '"')
    body = _CITATION.sub("", _FOOTNOTE_BODY.sub("", _STAMP.sub("", body)))
    body = _LIST_MARK.sub("", body)
    body = _FOOTNOTE_REF.sub("", _HYPHEN_BREAK.sub(r"\1\2", _PAGE_NUMBER.sub("", body)))
    normalised = _NOT_LETTER_OR_DIGIT.sub("", body).lower()
    return normalised if len(normalised) >= MIN_LAW_CHARS else None


def law_digest(text: str) -> str | None:
    """SHA-256 of `normalised_law`; None when the text carries no act to compare."""
    normalised = normalised_law(text)
    return hashlib.sha256(normalised.encode()).hexdigest() if normalised is not None else None

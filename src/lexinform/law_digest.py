"""Versioned law fingerprints that retain numeric values and bounded technical evidence."""

import hashlib
import json
import re

from lexinform.law_technical import TechnicalEvidence, TechnicalText, technical_law

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
_CITATION = re.compile(
    r"\(\s*Dz\.\s*U\.(?:Dz\.|U\.|z|r\.|poz\.|Nr|i|oraz|późn\.|zm\.|ze|\.{2,}|[\d\s,–—…-])+\)",
    re.IGNORECASE,
)
_LIST_MARK = re.compile(r"(?:(?<=^)|(?<=[\s,;:]))(?:[a-z]{1,2}|\d{1,3}[a-z]?)\)", re.MULTILINE)
_HYPHEN_BREAK = re.compile(r"([^\W\d_])-\s*\n\s*([^\W\d_])")
_FOOTNOTE_REF = re.compile(r"(?<=[^\W\d_])\d{1,2}\)")
_NOT_LETTER_OR_DIGIT = re.compile(r"[\W_]+")
MIN_LAW_CHARS = 200
"""A body shorter than this, normalised, is a fragment and says nothing about the act."""
LAW_DIGEST_PREFIX = "law-v2:"


def normalised_law(text: str) -> str | None:
    """Numeric tokens keep their order, separators and signs alongside the complete wording."""
    body = readable_law(text)
    if body is None:
        return None
    normalised = _NOT_LETTER_OR_DIGIT.sub("", body).lower()
    numbers = re.findall(r"[+-]?\d+(?:[,.:/-]\d+)*|[<>≤≥=+/%]", body)
    return normalised + "\x1f" + json.dumps(numbers) if len(normalised) >= MIN_LAW_CHARS else None


def readable_law(text: str) -> str | None:
    """The act with its apparatus dropped and its wording kept, for a diff to quote."""
    prepared = prepare_law(text)
    return prepared.text if prepared is not None else None


def prepare_law(text: str) -> TechnicalText | None:
    """`readable_law` with the evidence for every fragment it dropped."""
    prepared = technical_law(text)
    if prepared is None:
        return None
    body = prepared.text.translate(_CHARACTERS).replace(",,", '"')
    evidence = list(prepared.evidence)
    for pattern, replacement, rule in (
        (_CITATION, "", "publication_citation"),
        (_LIST_MARK, "", "list_marker"),
        (_HYPHEN_BREAK, r"\1\2", "hyphenated_word"),
        (_FOOTNOTE_REF, "", "footnote_marker"),
    ):
        evidence.extend(
            TechnicalEvidence(rule, match.group(), match.expand(replacement))
            for match in pattern.finditer(body)
        )
        body = pattern.sub(replacement, body)
    return TechnicalText(body, tuple(evidence))


def law_digest(text: str) -> str | None:
    """SHA-256 of `normalised_law`; None when the text carries no act to compare."""
    normalised = normalised_law(text)
    return (
        LAW_DIGEST_PREFIX + hashlib.sha256(normalised.encode()).hexdigest()
        if normalised is not None
        else None
    )

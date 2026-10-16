import datetime as dt
import re
import unicodedata
from typing import Literal

from lexinform.models.senate import SenateResolutionEvidence
from lexinform.sections import PAGE_BREAK, strip_page_furniture


def _word(value: str) -> str:
    return r"[ \t]*".join(value)


def _phrase(value: str) -> str:
    return r"\s+".join(_word(word) for word in value.split())


_HEADING = re.compile(
    rf"(?:^|(?<=\n)|(?<=\f))[ \t]*{_word('UCHWAŁA')}[ \t]*\n"
    rf"[ \t]*{_phrase('SENATU RZECZYPOSPOLITEJ POLSKIEJ')}\b",
    re.I,
)
_INTRO = re.compile(r"\bSenat,?\s+po\s+rozpatrzeniu\b", re.I)
_SUBJECT = re.compile(r"\s*\bw\s+sprawie\s+ustawy\b", re.I)
_AMENDMENTS = re.compile(
    rf"\b{_phrase('wprowadza do jej tekstu')}\s+"
    rf"(?:{_phrase('następujące poprawki')}|{_phrase('następującą poprawkę')})\s*[:.]",
    re.I,
)
_SIGNATURE = re.compile(
    rf"(?:^|(?<=\n)|(?<=\f))[ \t]*(?:{_word('WICE')})?"
    rf"{_phrase('MARSZAŁEK SENATU')}[ \t]*(?:\n|$)",
    re.I,
)
_JUSTIFICATION = re.compile(
    rf"(?:^|(?<=\n)|(?<=\f))[ \t]*{_word('UZASADNIENIE')}[ \t]*(?:\n|$)", re.I
)
_COVER = re.compile(rf"\b{_phrase('mam zaszczyt przekazać')}\b", re.I)
_OTHER_HEADER = re.compile(
    r"(?:^|(?<=\n)|(?<=\f))\s*(?:USTAWA|UZASADNIENIE|SPRAWOZDANIE|OPINIA)\b", re.I
)
_OPERATIONS = (
    "skreśla się",
    "dodaje się",
    "zastępuje się",
    "otrzymuje brzmienie",
    "otrzymują brzmienie",
    "zmienia się",
    "oznacza się",
    "zmniejsza się",
    "zwiększa się",
)
_OPERATION = re.compile(rf"\b(?:{'|'.join(_phrase(p) for p in _OPERATIONS)})\b", re.I)
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
_DATE = re.compile(
    rf"\s*z\s+dnia\s+(\d{{1,2}})\s+({'|'.join(_word(m) for m in _MONTHS)})"
    r"\s+(\d{4})\s*r\.",
    re.I,
)


def senate_subject_key(value: str) -> str:
    value = unicodedata.normalize("NFC", value).casefold().replace("\u00ad", "")
    value = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", value)
    parts = re.split(r"(\d+(?:[.,/]\d+)*)", value)
    return "".join(
        f"<{part}>" if index % 2 else "".join(char for char in part if char.isalnum())
        for index, part in enumerate(parts)
    )


def senate_subject(title: str) -> str | None:
    match = _SUBJECT.search(title)
    return title[match.end() :].strip().rstrip(".") if match else None


def inspect_senate_resolution(text: str) -> SenateResolutionEvidence:
    headings = list(_HEADING.finditer(text))
    if not headings:
        return SenateResolutionEvidence(limitation="no_resolution_heading")
    if len(headings) != 1:
        return SenateResolutionEvidence(limitation="multiple_resolutions")
    heading = headings[0]
    prefix = text[: heading.start()]
    if prefix.strip() and (
        _COVER.search(prefix) is None
        or not re.search(r"\bSenatu\b", prefix, re.I)
        or _OTHER_HEADER.search(prefix) is not None
    ):
        return SenateResolutionEvidence(limitation="non_cover_prefix")
    date_match = _DATE.match(text, heading.end())
    if date_match is None:
        return SenateResolutionEvidence(limitation="missing_resolution_date")
    month = re.sub(r"\s+", "", date_match[2]).casefold()
    try:
        adopted_on = dt.date(int(date_match[3]), _MONTHS.index(month) + 1, int(date_match[1]))
    except ValueError:
        return SenateResolutionEvidence(limitation="invalid_resolution_date")
    subject_start = _SUBJECT.match(text, date_match.end())
    intro = _INTRO.search(text, date_match.end())
    if subject_start is None or intro is None or intro.start() <= subject_start.end():
        return SenateResolutionEvidence(limitation="missing_resolution_subject")
    subject = text[subject_start.end() : intro.start()].strip()
    base = SenateResolutionEvidence(limitation="", subject=subject, adopted_on=adopted_on)
    signature = _SIGNATURE.search(text, intro.end())
    if signature is None:
        return base.model_copy(update={"limitation": "missing_resolution_end"})
    decision_text = text[intro.end() : signature.start()]
    operative = _AMENDMENTS.search(decision_text)
    if operative is None:
        outcome: Literal["amendments", "rejection", "acceptance"] | None = (
            "rejection"
            if re.search(r",\s*odrzuca\s+(?:tę\s+)?ustawę\s*\.\s*$", decision_text, re.I)
            else "acceptance"
            if re.search(
                r",\s*przyjmuje\s+(?:tę\s+)?ustawę\s+bez\s+poprawek\s*\.\s*$",
                decision_text,
                re.I,
            )
            else None
        )
        if outcome is None:
            return base.model_copy(update={"limitation": "unknown_resolution_decision"})
        return base.model_copy(update={"outcome": outcome, "text": text[heading.start() :]})
    recitation = decision_text[: operative.start()]
    recited_subject = re.search(r"\br\s*\.\s*ustawy\b", recitation, re.I)
    if recited_subject is None or senate_subject_key(
        recitation[recited_subject.end() :]
    ) != senate_subject_key(subject):
        return base.model_copy(update={"limitation": "inconsistent_resolution_subject"})
    body = decision_text[operative.end() :]
    if _JUSTIFICATION.search(body) is not None or _OPERATION.search(body) is None:
        return base.model_copy(update={"limitation": "missing_amendment_body"})
    for page in body.split(PAGE_BREAK)[1:-1]:
        remaining = strip_page_furniture(page, None)
        if not re.search(r"[a-ząćęłńóśźż]", remaining, re.I):
            return base.model_copy(update={"limitation": "unreadable_amendment_page"})
    return base.model_copy(
        update={
            "outcome": "amendments",
            "text": text[heading.start() :],
            "operative_text": body,
            "justification": _JUSTIFICATION.search(text, signature.end()) is not None,
        }
    )

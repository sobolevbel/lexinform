import datetime as dt
import re
import unicodedata
from typing import Literal

from lexinform.models.sejm import Stage, flatten_stages
from lexinform.models.senate import (
    AmendmentSourceEvidence,
    SenateResolutionEvidence,
    SenateResolutionIdentity,
)
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
_DATE_VALUE = rf"(\d{{1,2}})\s+({'|'.join(_word(m) for m in _MONTHS)})\s+(\d{{4}})\s*r\."
_DATE = re.compile(rf"\s*z\s+dnia\s+{_DATE_VALUE}", re.I)
_PASSED = re.compile(rf"\bw\s+dniu\s+{_DATE_VALUE}\s*ustawy\s+", re.I)
_JUSTIFICATION_INTROS = tuple(
    re.compile(pattern, re.I | re.S)
    for pattern in (
        rf"\s*Senat,?\s+po\s+rozpatrzeniu\s+ustawy\s+z\s+dnia\s+{_DATE_VALUE}\s+"
        r"(?P<subject>.+?),\s*postanowił\s+wprowadzić\b",
        rf"\s*Senat,?\s+po\s+rozpatrzeniu\s+uchwalonej\s+przez\s+Sejm\s+"
        rf"(?:na\s+posiedzeniu\s+)?w\s+dniu\s+{_DATE_VALUE}\s*ustawy\s+"
        r"(?P<subject>.+?)\s*,?\s*wprowadził\s+do\s+niej\b",
        rf"\s*Na\s+posiedzeniu\s+w\s+dniu\s+{_DATE_VALUE}\s*Senat\s+rozpatrzył\s+ustawę\s+"
        r"(?P<subject>.+?)\s+i\s+uchwalił\b",
        rf"\s*Sejm\s+przyjmując\s+{_DATE_VALUE}\s*ustawę\s+"
        r"(?P<subject>.+?),\s*wprowadził\b",
    )
)
_SINGLE_REMOVAL = re.compile(
    r"\s*[–—-]?\s*w\s+art\.\s*\d+[a-z]*\s+skreśla\s+się\s+"
    r"(?:pkt|ust\.)\s+(?:\d+[a-z]*|[–—-]|,|\s|pkt|ust\.|lit\.\s*[a-z]|oraz|i)+\.\s*",
    re.I,
)


def _date_value(match: re.Match[str]) -> dt.date | None:
    month = re.sub(r"\s+", "", match[2]).casefold()
    try:
        return dt.date(int(match[3]), _MONTHS.index(month) + 1, int(match[1]))
    except ValueError:
        return None


def _justified_subject(text: str, start: int) -> str | None:
    heading = _JUSTIFICATION.search(text, start)
    if heading is None:
        return None
    paragraph = text[heading.end() :].lstrip().split("\n\n", 1)[0]
    for pattern in _JUSTIFICATION_INTROS:
        match = pattern.match(paragraph)
        if match:
            return match["subject"].strip()
    return None


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


def resolution_date_matches(
    actual: dt.date, *, stage: dt.date, document: dt.date | None, passed: dt.date | None
) -> bool:
    return actual in {stage, document} or bool(
        passed is not None and document is not None and passed <= actual <= document
    )


def adoption_before_senate(
    stages: tuple[Stage, ...] | list[Stage], before: dt.date | None
) -> dt.date | None:
    return max(
        (
            stage.date
            for stage in flatten_stages(stages)
            if stage.stage_type == "SejmReading"
            and stage.date is not None
            and (before is None or stage.date <= before)
            and (stage.decision or "").strip().casefold().startswith("uchwalono")
        ),
        default=None,
    )


def validate_senate_scan(
    evidence: AmendmentSourceEvidence | None, source: SenateResolutionIdentity
) -> None:
    if evidence is None or not evidence.complete or evidence.decision != "amendments":
        raise ValueError("Модель не подтвердила полный текст постановления с поправками")
    if senate_subject_key(evidence.subject) not in {
        senate_subject_key(subject) for subject in source.expected_subjects
    }:
        raise ValueError("Постановление в скане относится к другому закону")
    if evidence.adopted_on is None or not resolution_date_matches(
        evidence.adopted_on,
        stage=source.adopted_on,
        document=source.document_date,
        passed=source.passed_on,
    ):
        raise ValueError("Дата постановления в скане не подтверждена")
    pages = evidence.amendment_pages
    if (
        evidence.resolution_page is None
        or not pages
        or not 1 <= evidence.resolution_page <= pages[0] <= pages[-1] <= source.pages
        or pages != list(range(pages[0], pages[-1] + 1))
        or len(evidence.amendment_quote.strip()) < 16
        or _OPERATION.search(evidence.amendment_quote) is None
    ):
        raise ValueError("Не подтверждены страницы и цитата самих поправок в скане")


def inspect_senate_resolution(text: str, *, word_eof: bool = False) -> SenateResolutionEvidence:
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
    adopted_on = _date_value(date_match)
    if adopted_on is None:
        return SenateResolutionEvidence(limitation="invalid_resolution_date")
    subject_start = _SUBJECT.match(text, date_match.end())
    intro = _INTRO.search(text, date_match.end())
    if subject_start is None or intro is None or intro.start() <= subject_start.end():
        return SenateResolutionEvidence(limitation="missing_resolution_subject")
    subject = text[subject_start.end() : intro.start()].strip()
    base = SenateResolutionEvidence(
        limitation="", subject=subject, heading_subject=subject, adopted_on=adopted_on
    )
    signature = _SIGNATURE.search(text, intro.end())
    ending = len(text) if signature is None else signature.start()
    decision_text = text[intro.end() : ending]
    operative = _AMENDMENTS.search(decision_text)
    if signature is None and (
        not word_eof
        or operative is None
        or "poprawkę" not in re.sub(r"\s+", "", operative[0]).casefold()
        or _SINGLE_REMOVAL.fullmatch(decision_text[operative.end() :]) is None
    ):
        return base.model_copy(update={"limitation": "missing_resolution_end"})
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
    passed = _PASSED.search(recitation)
    if passed is None:
        return base.model_copy(update={"limitation": "missing_sejm_adoption"})
    recited_subject = recitation[passed.end() :].strip().rstrip(",").strip()
    if re.search(rf"\b{_word('nie')}\s*$", recited_subject, re.I):
        return base.model_copy(update={"limitation": "negative_amendment_decision"})
    base = base.model_copy(
        update={"recited_subject": recited_subject, "passed_on": _date_value(passed)}
    )
    if senate_subject_key(recited_subject) != senate_subject_key(subject):
        corroboration = _justified_subject(text, ending)
        matching = next(
            (
                value
                for value in (subject, recited_subject)
                if corroboration and senate_subject_key(value) == senate_subject_key(corroboration)
            ),
            None,
        )
        if matching is None:
            return base.model_copy(update={"limitation": "inconsistent_resolution_subject"})
        base = base.model_copy(update={"subject": matching, "corroborated_subject": True})
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
            "justification": signature is not None
            and _JUSTIFICATION.search(text, signature.end()) is not None,
            "word_eof": signature is None,
        }
    )

"""Recognize a separately filed self-amendment directed at one RCL project."""

import re

from lexinform.models.documents import DocumentPart
from lexinform.models.rcl import normalize_wykaz_number

_HEADING = re.compile(
    r"\A\s*(?:\d+[ \t]*\n\s*)?(?:projekt(?:[ \t]+z[ \t]+dnia[ \t]+[^\n]{1,48})?[ \t]*\n\s*)?"
    r"autopoprawka\s+(?:[IVX]+\s+)?do\s+(?:rządowego\s+)?"
    r"projektu\s+ustawy\b(?:\s+projektu\s+ustawy\b)?\s*[–—-]?\s*",
    re.IGNORECASE,
)
_BODY = re.compile(r"^\s*(?:w\s+(?:rządowym\s+)?projekcie\b|art\s*\.|\d+\))", re.I | re.M)
_SUPPORT = re.compile(r"^\s*(?:uzasadnienie|ocena skutków regulacji)\s*$", re.I | re.M)
_PROJECT_NUMBER = re.compile(r"\b(?:UA|UB|UC|UD(?:ER)?|UO|UPRO)\s*\d+\b", re.I)
_JOINT_TARGET = re.compile(r"\bprojektu\s+ustawy\b", re.I)
_PRINT_REFERENCE = re.compile(r"\s*\(\s*(?:nr\s+)?druku?\b[^()]*\)", re.I)
_TARGET_NUMBER = re.compile(r"\(\s*([A-Z]+\s*\d+)\s*\)$", re.I)
_ARTICLE_FORMULA = re.compile(
    r"\bart\s*\.\s*\d+[a-z]?[\s\w.(),–-]{0,95}"
    r"(?:otrzymuje\s+brzmienie|dodaje\s+się|uchyla\s+się)\b",
    re.I,
)


def _title(text: str) -> str:
    words = " ".join(re.findall(r"[^\W_]+", text.casefold()))
    return re.sub(r"^(?:rządowy )?projekt ustawy ", "", words)


def _addresses_project(part: DocumentPart, project_title: str, project_number: str | None) -> bool:
    opening = part.text[:1200]
    heading = _HEADING.match(opening)
    if heading is None or (body := _BODY.search(opening, heading.end())) is None:
        return False
    target = opening[heading.end() : body.start()].strip()
    if not target or _JOINT_TARGET.search(target):
        return False
    numbers = {normalize_wykaz_number(match[0]) for match in _PROJECT_NUMBER.finditer(target)}
    title = _PRINT_REFERENCE.sub("", target).strip()
    if numbers:
        reference = _TARGET_NUMBER.search(title)
        if (
            reference is None
            or numbers != {project_number}
            or (normalize_wykaz_number(reference[1]) != project_number)
        ):
            return False
    elif _title(title) != project_title:
        return False
    normative = _SUPPORT.split(part.text[body.start() :], maxsplit=1)[0]
    return _ARTICLE_FORMULA.search(normative) is not None


def rcl_self_amendment_part(
    parts: tuple[DocumentPart, ...], *, project_title: str, wykaz_number: str | None
) -> DocumentPart | None:
    """Return a unique physical member with a single explicit project target and proposed text."""
    title = _title(project_title)
    number = normalize_wykaz_number(wykaz_number)
    if not title:
        return None
    matches = [
        part
        for part in parts
        if part.members and not part.losses and _addresses_project(part, title, number)
    ]
    return matches[0] if len(matches) == 1 else None

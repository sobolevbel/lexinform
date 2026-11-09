"""Recognize a separately filed self-amendment directed at one RCL project."""

import hashlib
import io
import re
import zipfile

from lexinform.models.documents import DocumentPart, DocumentSelection
from lexinform.models.extractions import DocumentExtraction
from lexinform.models.rcl import normalize_wykaz_number, rcl_num_key
from lexinform.models.sejm import ProcessSummary
from lexinform.ports import PdfPageTextExtractor
from lexinform.sections import PAGE_BREAK

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
_PRINT_REFERENCE = re.compile(r"\s*\(\s*(?:nr\.?\s+)?druku?\b[^()]*\)", re.I)
_PRINT_NUMBER = re.compile(
    r"\(\s*(?:nr\.?\s+)?druku?(?:\s+sejmow(?:y|ego))?"
    r"(?:\s+nr\.?)?\s+([1-9]\d*)\s*\)",
    re.I,
)
_TARGET_NUMBER = re.compile(r"\(\s*([A-Z]+\s*\d+)\s*\)$", re.I)
_ARTICLE_FORMULA = re.compile(
    r"\bart\s*\.\s*\d+[a-z]?[\s\w.(),–-]{0,95}"
    r"(?:otrzymuje\s+brzmienie|dodaje\s+się|uchyla\s+się)\b",
    re.I,
)
_TRANSMITTAL = re.compile(
    r"\b(?:w\s+załączeniu\s+(?:przekazuję|przedkładam)|"
    r"(?:przekazuję|przedkładam)\s+w\s+załączeniu)\s+"
    r"(?:projekt\s+autopoprawki|autopoprawkę)\b",
    re.I,
)
_NORMATIVE_REFERENCE = re.compile(
    r"\b(?:art\s*\.|artykuł|ust\s*\.|pkt\s*\.?|paragraf)\s*\d|§"
    r"|\b(?:otrzymuj(?:e|ą)\s+brzmienie|nadać\s+brzmienie|"
    r"uchyla\s+się|skreśla\s+się|zastępuje\s+się|dodaje\s+się)\b",
    re.I,
)


_MINISTRY_COMMENT_TITLE = re.compile(
    r"stanowisko ministra [^()]{1,80}? wobec uwag zgłoszonych do projektu ustawy "
    r"[^()]{1,400} \((?:UA|UB|UC|UD(?:ER)?|UO|UPRO)\d{1,5}\)",
    re.I,
)
_MINISTRY_COMMENT_COLUMNS = re.compile(
    r" w ramach skrm lp\. podmiot wnosząc ?y uwagi jednostka redakcyjna, "
    r"do której wnoszone są uwagi zgłoszona uwaga stanowisko\b"
)
_SEPARATE_NORMATIVE_HEADING = re.compile(
    r"^[ \t]*(?:autopoprawka\b|(?:projekt[ \t]+)?ustawa[ \t]*(?:z dnia\b|$))",
    re.I | re.M,
)


def _ministerial_comment_table_parts(parts: tuple[DocumentPart, ...]) -> bool:
    if len(parts) != 2 or any(
        len(part.members) != 1
        or "/" in part.members[0]
        or "\\" in part.members[0]
        or not part.members[0].casefold().endswith(".pdf")
        or not part.sha256
        or not part.pages
        or part.losses
        for part in parts
    ):
        return False
    for letter, table in (parts, parts[::-1]):
        if (
            letter.pages != 1
            or len(letter.text) > 2_000
            or "autopoprawk" in letter.text.casefold()
            or _NORMATIVE_REFERENCE.search(letter.text)
            or _SEPARATE_NORMATIVE_HEADING.search(table.text)
        ):
            continue
        pages = table.text.split(PAGE_BREAK)
        if len(pages) != table.pages or any(not page.strip() for page in pages):
            continue
        opening = " ".join(pages[0][:1_200].casefold().split())
        title = _MINISTRY_COMMENT_TITLE.match(opening)
        if title is not None and _MINISTRY_COMMENT_COLUMNS.match(opening, title.end()):
            forwarding = (
                f"szanowny panie sekretarzu, w załączeniu przekazuję tabelę zawierającą {title[0]} "
                "w ramach rozpatrywania przez stały komitet rady ministrów. z wyrazami szacunku,"
            )
            if forwarding in " ".join(letter.text.casefold().split()):
                return True
    return False


def complete_ministerial_comment_table_bundle(
    pdf: PdfPageTextExtractor, data: bytes, extracted: DocumentExtraction
) -> bool:
    selection, result = extracted.document.selection, extracted.result
    parts = selection.observed_parts
    if (
        selection.state != "unsupported"
        or selection.parts
        or extracted.document.archive_unpack_blocked
        or result.physical_part_count != 2
        or result.rejected_count
        or not _ministerial_comment_table_parts(parts)
        or any(part.source_sha256 != result.artifact_sha256 for part in parts)
    ):
        return False
    members = {part.members[0]: part for part in parts}
    if len(members) != 2 or hashlib.sha256(data).hexdigest() != result.artifact_sha256:
        return False
    max_member_bytes = result.options.max_member_bytes
    if max_member_bytes is None:
        max_member_bytes = result.options.archive_bytes
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if (
                len(entries) != len(members)
                or {entry.filename for entry in entries} != members.keys()
                or any(entry.file_size > max_member_bytes for entry in entries)
                or sum(entry.file_size for entry in entries) > result.options.archive_bytes
            ):
                return False
            for entry in entries:
                part = members[entry.filename]
                body = archive.read(entry)
                if hashlib.sha256(body).hexdigest() != part.sha256:
                    return False
                pages = pdf.extract_pages(body)
                if (
                    pages.input_sha256 != part.sha256
                    or pages.page_count != part.pages
                    or pages.legacy_text != part.text
                    or any(
                        page.error_type is not None
                        or not page.text.strip()
                        or PAGE_BREAK in page.text
                        for page in pages.pages
                    )
                ):
                    return False
    except OSError, ValueError, RuntimeError, zipfile.BadZipFile:
        return False
    return True


def _title(text: str) -> str:
    words = " ".join(re.findall(r"[^\W_]+", text.casefold()))
    return re.sub(r"^(?:rządowy )?projekt ustawy ", "", words)


def _opening_target(text: str) -> tuple[str, int] | None:
    opening = text[:1200]
    heading = _HEADING.match(opening)
    if heading is None or (body := _BODY.search(opening, heading.end())) is None:
        return None
    return opening[heading.end() : body.start()].strip(), body.start()


def rcl_self_amendment_print_number(text: str) -> str | None:
    """Return the explicit original print reference from a single-target proposal heading."""
    opening = _opening_target(text)
    if opening is None or _JOINT_TARGET.search(opening[0]):
        return None
    references = _PRINT_REFERENCE.findall(opening[0])
    if len(references) != 1:
        return None
    match = _PRINT_NUMBER.fullmatch(references[0].strip())
    return match[1] if match else None


def _has_article_formula(text: str, start: int) -> bool:
    normative = _SUPPORT.split(text[start:], maxsplit=1)[0]
    return _ARTICLE_FORMULA.search(normative) is not None


def has_rcl_self_amendment_text(text: str) -> bool:
    """Recognize proposed article text before any separate supporting section."""
    opening = _opening_target(text)
    return opening is not None and _has_article_formula(text, opening[1])


def is_rcl_amendment_transmittal(text: str) -> bool:
    """Identify a letter explicitly forwarding a separate self-amendment."""
    return (
        _TRANSMITTAL.search(text[:1600]) is not None
        and _HEADING.match(text[:1200]) is None
        and _NORMATIVE_REFERENCE.search(text) is None
    )


def _addresses_project(part: DocumentPart, project_title: str, project_number: str | None) -> bool:
    opening = _opening_target(part.text)
    if opening is None:
        return False
    target, body_start = opening
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
    return _has_article_formula(part.text, body_start)


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


def _edition_body(selection: DocumentSelection) -> str | None:
    if selection.state not in ("selected", "unverified"):
        return None
    normative = tuple(
        part
        for part in selection.parts
        if part.role in ("bill", "normative_annex", "normative_continuation")
    )
    if len(normative) != 1 or normative[0].role != "bill" or normative[0].losses:
        return None
    return normative[0].text.strip() or None


def rcl_amendment_base_index(
    proposal: DocumentPart,
    *,
    project_rm_number: str | None,
    print_process: ProcessSummary,
    original: DocumentSelection,
    candidates: tuple[DocumentSelection, ...],
) -> int | None:
    """Resolve one base only from an explicit same-project print and exact complete bill text."""
    rm_number = rcl_num_key(project_rm_number or "")
    if (
        not proposal.members
        or proposal.losses
        or rcl_self_amendment_print_number(proposal.text) != print_process.number
        or not rm_number
        or rm_number != rcl_num_key(print_process.rcl_num or "")
    ):
        return None
    if len(original.parts) != 1 or original.parts[0].members:
        return None
    expected = _edition_body(original)
    bodies = tuple(_edition_body(candidate) for candidate in candidates)
    if expected is None or None in bodies:
        return None
    matches = tuple(index for index, body in enumerate(bodies) if body == expected)
    return matches[0] if len(matches) == 1 else None

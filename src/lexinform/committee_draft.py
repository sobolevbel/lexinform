"""Identify the bounded bill attachment in a complete Sejm committee report."""

import hashlib
import re
from datetime import date

from lexinform.models.document_history import DocumentSelectionRecord
from lexinform.models.document_reads import LoadedFile
from lexinform.models.documents import DocumentPart, DocumentSelection
from lexinform.models.reviews import date_in_quote
from lexinform.models.sejm import SejmCommitteeDraftOrigin, TextDocument
from lexinform.sections import PAGE_BREAK

_STAMP = re.compile(
    r"^Liczba stron : (?P<count>[1-9][0-9]*) Data : (?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2}) "
    r"Nazwa pliku : (?P<file>\S+) (?P<page>[1-9][0-9]*) X kadencja/druk "
    r"(?:nr )?(?P<owner>[1-9][0-9]*)\b"
)
_OPENING = re.compile(r"^ Projekt U S T AWA z dnia .+?\bArt\. 1\. ")
_FINAL = re.compile(r"\bArt\. [1-9][0-9]*\. Ustawa wchodzi w życie [^;“”„]{1,1200}\.$")
_COVER_DATE = re.compile(r"\bWarszawa, dnia ([0-9]{1,2} \w+ [0-9]{4}) r\.")


def committee_draft_selection(
    origin: SejmCommitteeDraftOrigin, document: TextDocument, file: LoadedFile
) -> DocumentSelectionRecord | None:
    if (
        origin.term != 10
        or document != TextDocument(url=origin.url, kind="committee_report")
        or file.text is None
        or file.complete_pdf_pages is None
        or len(file.selection.parts) != 1
    ):
        return None
    (part,) = file.selection.parts
    if (
        part.members
        or part.url != document.url
        or file.sha256 != part.sha256
        or part.text != file.text
    ):
        return None
    pages = file.text.split(PAGE_BREAK)
    if len(pages) != file.complete_pdf_pages:
        return None
    cover = " ".join(pages[0].split())
    if (
        re.match(rf"Druk nr {origin.report}\b.*?\bS P R A W O Z D A N I E KOMISJI\b", cover) is None
        or re.findall(r"\(druk nr ([0-9]+)\)", cover) != [origin.number]
        or "uchwalić raczy załączony projekt ustawy." not in cover
    ):
        return None
    flat = [" ".join(page.split()) for page in pages]
    openings = [
        index
        for index, page in enumerate(flat)
        if (stamp := _STAMP.match(page)) is not None
        and stamp["page"] == "1"
        and _OPENING.match(page[stamp.end() :]) is not None
    ]
    if len(openings) != 1:
        return None
    (first,) = openings
    opening = _STAMP.match(flat[first])
    assert opening is not None, "An opening requires the printed page stamp"
    if first < 1 or len(pages) - first < 2 or int(opening["count"]) != len(pages) - first:
        return None
    stamp_date = date.fromisoformat(opening["date"])
    cover_date = _COVER_DATE.search(cover)
    if stamp_date != origin.report_date and (
        cover_date is None
        or not date_in_quote(origin.report_date, cover_date[1])
        or not 0 <= (stamp_date - origin.report_date).days <= 7
    ):
        return None
    for number, page in enumerate(flat[first:], 1):
        stamp = _STAMP.match(page)
        if stamp is None or (
            stamp["owner"],
            stamp["count"],
            stamp["date"],
            stamp["file"],
            stamp["page"],
        ) != (origin.number, opening["count"], opening["date"], opening["file"], str(number)):
            return None
    if _FINAL.search(flat[-1]) is None:
        return None
    text = PAGE_BREAK.join(pages[first:]).strip()
    return DocumentSelectionRecord(
        selection=DocumentSelection(
            question="sejm_committee_draft",
            policy_version="sejm-committee-draft-pages-v1",
            parts=(
                DocumentPart(
                    url=part.url,
                    sha256=part.sha256,
                    role="bill",
                    pages=len(pages),
                    page_start=first + 1,
                    page_end=len(pages),
                    text_end=len(text),
                    text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                ),
            ),
        )
    )

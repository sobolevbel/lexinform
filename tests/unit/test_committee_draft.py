import datetime as dt
import hashlib

import pytest

from lexinform.committee_draft import committee_draft_selection
from lexinform.models import DocumentPart, DocumentSelection, TextDocument
from lexinform.models.document_reads import LoadedFile
from lexinform.models.sejm import SejmCommitteeDraftOrigin
from lexinform.sections import PAGE_BREAK

ORIGIN = SejmCommitteeDraftOrigin(10, "2111", "2594", dt.date(2026, 5, 26))
DOCUMENT = TextDocument(url=ORIGIN.url, kind="committee_report")
COVER = (
    "Druk nr 2594 X kadencja S P R A W O Z D A N I E KOMISJI "
    "(druk nr 2111) uchwalić raczy załączony projekt ustawy."
)
STAMP = "Liczba stron : 2 Data : 2026-05-26 Nazwa pliku : projekt "
OPENING = STAMP + "1 X kadencja/druk 2111 Projekt U S T AWA z dnia Tytuł Art. 1. Tekst."
ENDING = STAMP + "2 X kadencja/druk 2111 Art. 2. Ustawa wchodzi w życie z dniem ogłoszenia."


def loaded(pages: tuple[str, ...], *, complete: bool = True) -> LoadedFile:
    text = PAGE_BREAK.join(pages)
    sha = hashlib.sha256(text.encode()).hexdigest()
    return LoadedFile(
        text,
        sha256=sha,
        selection=DocumentSelection(parts=(DocumentPart(url=ORIGIN.url, sha256=sha, text=text),)),
        complete_pdf_pages=len(pages) if complete else None,
    )


def test_draft_has_its_own_metadata_only_pdf_range() -> None:
    file = loaded((COVER, OPENING, ENDING))

    proof = committee_draft_selection(ORIGIN, DOCUMENT, file)

    assert proof is not None, "The exact stamped draft is complete"
    (part,) = proof.selection.parts
    assert (part.page_start, part.page_end, part.role) == (2, 3, "bill")
    assert part.text == ""
    assert part.text_sha256 == hashlib.sha256((OPENING + PAGE_BREAK + ENDING).encode()).hexdigest()


@pytest.mark.parametrize(
    ("cover", "opening", "ending", "complete"),
    [
        (COVER.replace("druk nr 2111", "druk nr 2112"), OPENING, ENDING, True),
        (COVER, OPENING, ENDING.replace("2 X kadencja", "1 X kadencja"), True),
        (COVER, OPENING, ENDING.replace("2026-05-26", "2026-05-27"), True),
        (COVER, OPENING, ENDING + " Załącznik", True),
        (COVER, OPENING, ENDING, False),
    ],
)
def test_draft_requires_exact_cover_and_complete_stamped_page_sequence(
    cover: str, opening: str, ending: str, complete: bool
) -> None:
    assert (
        committee_draft_selection(
            ORIGIN, DOCUMENT, loaded((cover, opening, ending), complete=complete)
        )
        is None
    )

import datetime as dt

import pytest
from tools.replay_proposal_edition import PinnedInput
from tools.replay_sejm_supersession import SupersessionCase, prove_bodies

from lexinform.models import PdfPageText, PdfTextExtraction

PIN = PinnedInput(path="test.pdf", sha256="a" * 64)
CASE = SupersessionCase(
    term=10,
    owner="2111",
    report="2594",
    adoption_index=6,
    report_draft_page=2,
    processes=PIN,
    prints=PIN,
    report_pdf=PIN,
    adopted_pdf=PIN,
)
COVER = "Druk nr 2594 X kadencja S P R A W O Z D A N I E KOMISJI (druk nr 2111) uchwalić raczy załączony projekt ustawy."
DRAFT = (
    "Liczba stron : 2 Data : 2026-05-26 Nazwa pliku : projekt 1 "
    "X kadencja/druk 2111 Projekt U S T AWA z dnia Przepisy wprowadzające ustawę "
    "Rozdział 1 Przepis ogólny Art. 1. Przepis."
)
ADOPTED = (
    "Tekst ustawy przekazany do Senatu zgodnie z art. 52 regulaminu Sejmu "
    "USTAWA z dnia 29 maja 2026 r. Przepisy wprowadzające ustawę "
    "Rozdział 1 Przepis ogólny Art. 1. Przepis."
)
COMMENCEMENT = (
    "Ustawa wchodzi w życie z dniem 1 stycznia 2027 r., z wyjątkiem art. 253, "
    "który wchodzi w życie z dniem następującym po dniu ogłoszenia."
)
SIGNATURE = " MARSZAŁEK SEJMU / – / Podpis"
REPORT_PAGES = (COVER, DRAFT, "Art. 254. " + COMMENCEMENT)
ADOPTED_PAGES = (ADOPTED, "Art. 257. " + COMMENCEMENT.replace("253", "256") + SIGNATURE)


def pdf(sha: str, pages: tuple[str, ...]) -> PdfTextExtraction:
    return PdfTextExtraction(
        input_sha256=sha * 64,
        pypdf_version="fixture",
        pages=tuple(PdfPageText(number=i, text=text) for i, text in enumerate(pages, 1)),
    )


def test_replay_pins_two_distinct_literal_pdf_boundaries() -> None:
    proof = prove_bodies(
        CASE,
        pdf("a", REPORT_PAGES),
        pdf("b", ADOPTED_PAGES),
        report_date=dt.date(2026, 5, 26),
        adoption_date=dt.date(2026, 5, 29),
    )

    assert proof["prior"] != proof["successor"]


@pytest.mark.parametrize(
    ("report", "adopted", "message"),
    [
        ((COVER.replace("2111", "2112"), *REPORT_PAGES[1:]), ADOPTED_PAGES, "cover"),
        ((COVER, DRAFT.replace("stron : 2", "stron : 3"), REPORT_PAGES[2]), ADOPTED_PAGES, "count"),
        ((COVER, DRAFT.replace("05-26", "05-25"), REPORT_PAGES[2]), ADOPTED_PAGES, "dates"),
        (REPORT_PAGES, (ADOPTED.replace("29 maja", "28 maja"), ADOPTED_PAGES[1]), "dates"),
        (REPORT_PAGES, (ADOPTED, "Art. 257. " + COMMENCEMENT + SIGNATURE), "distinct"),
        (REPORT_PAGES, (ADOPTED, ADOPTED_PAGES[1].removesuffix(SIGNATURE)), "boundaries"),
        (REPORT_PAGES, (ADOPTED, "", ADOPTED_PAGES[1]), "readable PDF page"),
    ],
)
def test_replay_refuses_unproved_boundaries_or_identity(
    report: tuple[str, ...], adopted: tuple[str, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        prove_bodies(
            CASE,
            pdf("a", report),
            pdf("b", adopted),
            report_date=dt.date(2026, 5, 26),
            adoption_date=dt.date(2026, 5, 29),
        )

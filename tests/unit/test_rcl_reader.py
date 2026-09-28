"""What `RclProjectReader` reads of a project: the bill's text and the consultation letters."""

import datetime as dt

from lexinform.models import RclConsultation, RclDocument, RclProject
from lexinform.services.documents import TextLoader
from lexinform.services.rcl_projects import RclProjectReader
from tests.fakes import FakeRclGateway, FakeTextExtractor
from tests.harness import RCL_HOST, RCL_ID, rcl_document, rcl_folder, rcl_project, rcl_stage

HANDOVER_LETTER = rcl_folder(13223970, "Pismo przewodnie", rcl_document(794900, "pismo.pdf"))
PROJECT_FOLDER = rcl_folder(13223896, "Projekt", rcl_document(794885, "projekt_ustawy.DOCX"))


def _reader(*stages: object) -> tuple[RclProjectReader, FakeRclGateway]:
    gateway = FakeRclGateway()
    project = rcl_project(stages=tuple(stages), consultation=None)
    gateway.put(project)
    loader = TextLoader(
        {"legislacja.rcl.gov.pl": gateway.download}, FakeTextExtractor(), max_bytes=10**6
    )
    return RclProjectReader(gateway, loader), gateway


def test_the_text_is_found_below_the_stage_the_project_ends_on() -> None:
    """The stages that work on a project republish its text; the hand-over to the Sejm carries
    the covering letter to the Marshal and no "Projekt" folder. Reading only the newest reached
    stage closed nine projects as "no document to read" in one run (production, 15 Sept 2026);
    over the corpus the newest stage has the text for 26% of projects and the next for 99%."""
    reader, gateway = _reader(
        rcl_stage(4, "Opiniowanie", "reached", PROJECT_FOLDER),
        rcl_stage(14, "Skierowanie projektu ustawy do Sejmu", "active", HANDOVER_LETTER),
    )

    project = reader.with_text(reader.timeline(RCL_ID))

    assert "bill" in project.text_documents()
    assert len([c for c in gateway.calls if c.startswith("get_stage")]) == 2


def test_the_newest_catalog_is_enough_when_it_carries_the_text() -> None:
    """The common shape, and the reason the walk stops instead of reading the timeline: a page
    takes some ten seconds."""
    reader, gateway = _reader(
        rcl_stage(3, "Konsultacje publiczne", "reached", PROJECT_FOLDER),
        rcl_stage(4, "Opiniowanie", "active", PROJECT_FOLDER),
    )

    project = reader.with_text(reader.timeline(RCL_ID))

    assert "bill" in project.text_documents()
    assert len([c for c in gateway.calls if c.startswith("get_stage")]) == 1


def test_a_project_with_no_text_anywhere_stops_at_the_cap() -> None:
    """35 of the 831 projects of the corpus carry no readable "Projekt" folder at all; the walk
    must not turn into reading the whole timeline for them."""
    reader, gateway = _reader(
        *(rcl_stage(n, f"Etap {n}", "reached", HANDOVER_LETTER) for n in range(2, 12))
    )

    project = reader.with_text(reader.timeline(RCL_ID))

    assert project.text_documents() == {}
    assert len([c for c in gateway.calls if c.startswith("get_stage")]) == 3


ORIGINAL = rcl_document(774306, "Pismo MC_Konsultacje_UDER87.docx", created=dt.date(2026, 4, 30))
CLOSING = rcl_document(
    799267, "Pismo MC_zakończenie_konsultacje.docx", created=dt.date(2026, 9, 28)
)
EXTENSION = rcl_document(780001, "Informacja o przedłużeniu terminu.docx")
GREETING = "Szanowni Państwo, przekazuję projekt ustawy o zmianie ustawy – Prawo o ruchu drogowym. "
LETTERS = {
    ORIGINAL: "Warszawa, 30 kwietnia 2026 r. " + GREETING + "Uprzejmie proszę o uwagi w terminie"
    " 30 dni od dnia otrzymania pisma; uwagi proszę przesyłać na adres: sekretariat.dp@cyfra.gov.pl",
    CLOSING: "Warszawa, 26 września 2026 r. " + GREETING + "Uprzejmie informuję, że w związku z"
    " zakończeniem konsultacji publicznych zostały udostępnione tabele ze stanowiskami do uwag.",
    EXTENSION: "Warszawa, 20 maja 2026 r. " + GREETING + "Informuję o przedłużeniu terminu"
    " konsultacji publicznych tego projektu do dnia 15 czerwca 2026 r.",
}


def _consulted(
    *letters: RclDocument, beside: RclDocument | None = None
) -> tuple[RclProjectReader, RclProject, FakeRclGateway]:
    """A project whose consultation stage files `letters` in their folder and `beside` with the bill."""
    project_docs = (rcl_document(794885, "projekt_ustawy.DOCX"), *([beside] if beside else []))
    stage = rcl_stage(
        3,
        "Konsultacje publiczne",
        "active",
        rcl_folder(13223896, "Projekt", *project_docs),
        rcl_folder(13223897, "Pisma kierujące projekt do konsultacji publicznych", *letters),
    )
    gateway = FakeRclGateway()
    gateway.put(rcl_project(stages=(stage,), consultation=None))
    extractor = FakeTextExtractor(by_content={_bytes(d): t for d, t in LETTERS.items()})
    for doc in (*project_docs, *letters):
        gateway.files[doc.url] = _bytes(doc)
    loader = TextLoader({RCL_HOST: gateway.download}, extractor, max_bytes=10**6)
    reader = RclProjectReader(gateway, loader)
    project = reader.timeline(RCL_ID).with_stage(gateway.get_stage(RCL_ID, stage.id))
    gateway.calls.clear()
    return reader, project, gateway


def _bytes(doc: RclDocument) -> bytes:
    """A Word file as the fake serves it: the zip signature, then something to tell it apart."""
    return b"PK\x03\x04 " + doc.url.encode()


def _downloads(gateway: FakeRclGateway) -> list[str]:
    return [c.split(":", 1)[1] for c in gateway.calls if c.startswith("download:")]


def test_a_closing_notice_does_not_erase_the_window_already_read() -> None:
    reader, project, gateway = _consulted(CLOSING, beside=ORIGINAL)
    known = RclConsultation(
        letter_url=ORIGINAL.url,
        deadline=dt.date(2026, 5, 30),
        email="sekretariat.dp@cyfra.gov.pl",
    )

    window = reader.consultation(project, known=known)
    again = reader.consultation(project, known=window)

    assert window is not None and again is not None
    assert (window.letter_url, window.deadline, window.email) == (
        ORIGINAL.url,
        dt.date(2026, 5, 30),
        "sekretariat.dp@cyfra.gov.pl",
    )
    assert _downloads(gateway) == [CLOSING.url]
    assert again == window


def test_a_window_taken_from_a_closing_notice_is_repaired_from_the_letter_beside_the_bill() -> None:
    reader, project, gateway = _consulted(CLOSING, beside=ORIGINAL)

    window = reader.consultation(project, known=RclConsultation(letter_url=CLOSING.url))

    assert window is not None
    assert (window.deadline, window.email) == (dt.date(2026, 5, 30), "sekretariat.dp@cyfra.gov.pl")
    assert set(window.letters_read) == {CLOSING.url, ORIGINAL.url}
    assert _downloads(gateway) == [ORIGINAL.url]


def test_an_extension_moves_the_deadline_and_keeps_the_address() -> None:
    reader, project, _ = _consulted(ORIGINAL, EXTENSION)

    window = reader.consultation(project)

    assert window is not None
    assert (window.deadline, window.email) == (dt.date(2026, 6, 15), "sekretariat.dp@cyfra.gov.pl")

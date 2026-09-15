"""How far back `RclProjectReader.with_text` looks for the bill, and what it costs."""

from lexinform.services.documents import TextLoader
from lexinform.services.rcl_projects import RclProjectReader
from tests.fakes import FakeRclGateway, FakeTextExtractor
from tests.harness import RCL_ID, rcl_document, rcl_folder, rcl_project, rcl_stage

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

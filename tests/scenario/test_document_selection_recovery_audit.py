import pytest

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models import BillStatus
from tests.fakes import FakeTextExtractor
from tests.harness import World, rcl_document, rcl_folder, rcl_project, rcl_stage
from tests.scenario.test_senate_document_selection import FIXTURES, world_with_sources

DOCUMENT_FIXTURES = FIXTURES.parent / "document_selection"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="B99: rejecting the regulation does not revisit the available bill",
)
def test_rejecting_a_regulation_reaches_the_available_bill_before_analysis() -> None:
    law = "USTAWA\nz dnia 7 kwietnia 2025 r.\nArt. 1. Cudzoziemiec składa wniosek. " * 20
    regulation = "ROZPORZĄDZENIE\nMINISTRA INFRASTRUKTURY\n" + "Przepisy wykonawcze. " * 40
    wrong = rcl_document(418609, "Projekt rozp ws wykazu stanowisk.docx")
    right = rcl_document(418603, "RM_UD530 na RM.docx")
    project = rcl_project(
        stages=(rcl_stage(1, "Projekt", "reached", rcl_folder(1, "Projekt", wrong, right)),),
        consultation=None,
    )
    world = World(extractor=FakeTextExtractor(by_content={b"regulation": regulation, b"law": law}))
    world.add_rcl_project(project)
    world.rcl.files[wrong.url] = b"regulation"
    world.rcl.files[right.url] = b"law"

    report = world.run(track=False)

    assert not report.errors and len(world.llm.contexts) == 1
    assert world.llm.contexts[0].text_source != "metadata_only"
    assert "Cudzoziemiec składa wniosek" in world.llm.contexts[0].text
    record = world.bill(f"RCL/{project.id}").analysis
    assert record is not None and record.source_url == right.url


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="B102: an unsupported recoverable archive becomes a terminal prefilter skip",
)
def test_recoverable_archive_failure_is_not_a_terminal_text_verdict(workers: int) -> None:
    extractor = DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())
    document = rcl_document(771111, "Projekt ustawy.zip")
    project = rcl_project(
        title="Projekt ustawy o zmianie niektórych ustaw",
        keywords=(),
        stages=(rcl_stage(1, "Projekt", "reached", rcl_folder(1, "Projekt", document)),),
        consultation=None,
    )
    world = World(extractor=extractor, workers=workers)
    world.add_rcl_project(project)
    world.rcl.files[document.url] = (DOCUMENT_FIXTURES / "deflate64_771111.zip").read_bytes()

    report = world.run(max_analyze=0, track=False)

    assert not report.errors and report.text_prefilter_checked == 1
    bill = world.bill(f"RCL/{project.id}")
    assert not (
        bill.status is BillStatus.SKIPPED_TEXT_PREFILTER and report.text_prefilter_unreadable
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="B104: a source that appears after the stage checkpoint is never selected again",
)
def test_late_senate_attachment_is_revisited_without_another_stage_change() -> None:
    resolution = (FIXTURES / "senate_3213_resolution.txt").read_text()
    world = world_with_sources({"3213.docx": resolution})
    del world.gateway.files["https://api.test/3213.docx"]

    world.run()
    assert not world.llm.amendment_contexts
    changes = world.repo.list_status_changes(10, "1929")
    saved_ids = [change.id for change in changes]
    world.gateway.files["https://api.test/3213.docx"] = b"3213.docx"
    world.clock.advance(days=1)
    world.run()

    assert len(world.llm.amendment_contexts) == 1
    assert world.llm.amendment_contexts[0].text == resolution
    assert [change.id for change in world.repo.list_status_changes(10, "1929")] == saved_ids

    world.clock.advance(days=1)
    world.run()

    assert len(world.llm.amendment_contexts) == 1
    assert [change.id for change in world.repo.list_status_changes(10, "1929")] == saved_ids

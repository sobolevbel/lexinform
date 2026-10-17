import datetime as dt

import pytest

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models import Attachment, BillStatus, PrintInfo, PublicationKind
from tests.fakes import FakeTextExtractor
from tests.harness import CHANNEL, REFERRED, World, rcl_document, rcl_folder, rcl_project, rcl_stage
from tests.scenario.test_senate_document_selection import (
    FIXTURES,
    STAGE,
    SUBJECT,
    world_with_sources,
)

DOCUMENT_FIXTURES = FIXTURES.parent / "document_selection"


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


@pytest.mark.parametrize("restore", [False, True])
def test_late_senate_attachment_is_revisited_without_another_stage_change(restore: bool) -> None:
    resolution = (FIXTURES / "senate_3213_resolution.txt").read_text()
    world = world_with_sources({"3213.docx": resolution})
    del world.gateway.files["https://api.test/3213.docx"]

    world.run()
    assert not world.llm.amendment_contexts
    changes = world.repo.list_status_changes(10, "1929")
    saved_ids = [change.id for change in changes]
    previous = world.bill("1929").analysis
    assert previous is not None
    saved_summary = previous.analysis.summary
    updated = previous.model_copy(deep=True)
    updated.analysis.summary = "Позднейший анализ, который не должен заменить baseline."
    world.repo.save_analysis(10, "1929", updated)
    world.repo.prune_history(
        before=world.clock.now() + dt.timedelta(days=365),
        memo_before=world.clock.now() + dt.timedelta(days=365),
    )
    if restore:
        world.repo.restore(world.repo.dump())
    world.gateway.files["https://api.test/3213.docx"] = b"3213.docx"
    world.clock.advance(days=1)
    world.run()

    assert len(world.llm.amendment_contexts) == 1
    assert world.llm.amendment_contexts[0].text == resolution
    assert world.llm.amendment_contexts[0].previous_summary == saved_summary
    assert [change.id for change in world.repo.list_status_changes(10, "1929")] == saved_ids

    world.clock.advance(days=1)
    world.run()

    assert len(world.llm.amendment_contexts) == 1
    assert [change.id for change in world.repo.list_status_changes(10, "1929")] == saved_ids

    original = world.repo.get_update_publication(saved_ids[-1] or 0, CHANNEL)
    assert original is not None and original.id is not None
    revisions = world.repo.list_amendment_revisions(original.id)
    assert len(revisions) == 1
    assert revisions[0].kind is PublicationKind.AMENDMENTS_REVISION


def test_late_batch_keeps_its_context_when_the_source_disappears_after_submission() -> None:
    resolution = (FIXTURES / "senate_3213_resolution.txt").read_text()
    url = "https://api.test/3213.docx"
    world = World(
        batch=True,
        batch_kinds=frozenset({"amendments"}),
        text_budget_chars=60_000,
        extractor=FakeTextExtractor(by_content={b"resolution": resolution}),
    )
    world.add_bill("1929", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    world.run()
    world.gateway.prints["3213"] = PrintInfo(
        term=10,
        number="3213",
        title="Uchwała Senatu w sprawie ustawy " + SUBJECT,
        attachments=(Attachment(print_number="3213", name="3213.docx", url=url),),
    )
    world.set_stages("1929", REFERRED + (STAGE,))
    world.clock.advance(days=1)
    world.run()
    world.gateway.files[url] = b"resolution"
    world.clock.advance(days=1)
    world.run()
    assert len(world.batch.submitted) == 1
    work = world.repo.list_document_work(due=world.clock.now())
    assert len(work) == 1 and work[0].context_json is not None
    world.repo.restore(world.repo.dump())
    del world.gateway.files[url]
    world.batch.resolve()

    world.run()
    world.run()

    assert len(world.batch.submitted) == 1
    assert not world.repo.list_document_work(due=world.clock.now())
    change = world.repo.list_status_changes(10, "1929")[-1]
    assert change.amendments is not None
    assert change.amendments.source_url == url

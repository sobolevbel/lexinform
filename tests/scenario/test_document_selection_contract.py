import datetime as dt

import pytest

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import Attachment, BillStatus, PrintInfo, TextDocument
from lexinform.services.sources import SejmTextSource
from tests.fakes import FakeTextExtractor
from tests.harness import World, rcl_document, rcl_folder, rcl_project, rcl_stage


def law(*, day: int = 1, days: int = 30) -> str:
    return (
        f"Projekt z dnia {day} września 2026 r.\nUSTAWA\nz dnia …\n"
        "o zmianie ustawy o cudzoziemcach\n"
        f"Art. 1. Cudzoziemiec składa wniosek w terminie {days} dni.\n"
        "Art. 2. Organ rozpatruje wniosek i doręcza decyzję stronie postępowania.\n"
        "Art. 3. Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia."
    )


@pytest.mark.parametrize("workers", [1, 4])
def test_internal_draft_version_beats_the_upload_date(workers: int) -> None:
    old = rcl_document(1, "Projekt ustawy.pdf", created=dt.date(2026, 9, 5))
    new = rcl_document(2, "Projekt ustawy.docx", created=dt.date(2026, 9, 4))
    project = rcl_project(
        stages=(rcl_stage(1, "Projekt", "reached", rcl_folder(1, "Projekt", old, new)),),
        consultation=None,
    )
    world = World(
        extractor=FakeTextExtractor(page_count=0, by_content={b"old": law(), b"new": law(day=2)}),
        workers=workers,
    )
    world.add_rcl_project(project)
    world.rcl.files.update({old.url: b"old", new.url: b"new"})

    world.run(track=False)

    record = world.bill(f"RCL/{project.id}").analysis
    assert record is not None and record.source_url == new.url
    assert world.llm.contexts[0].text == law(day=2)
    card = MessageFormatter("ru").new_bill(world.bill(f"RCL/{project.id}"), None).text
    assert new.url in card and old.url not in card


@pytest.mark.parametrize("workers", [1, 4])
def test_conflicting_word_and_pdf_cannot_be_resolved_by_format_rank(workers: int) -> None:
    pdf = rcl_document(1, "Projekt ustawy.pdf")
    word = rcl_document(2, "Projekt ustawy.docx")
    project = rcl_project(
        stages=(rcl_stage(1, "Projekt", "reached", rcl_folder(1, "Projekt", pdf, word)),),
        consultation=None,
    )
    world = World(
        extractor=FakeTextExtractor(
            page_count=0, by_content={b"pdf": law(), b"word": law(days=60)}
        ),
        workers=workers,
    )
    world.add_rcl_project(project)
    world.rcl.files.update({pdf.url: b"pdf", word.url: b"word"})

    world.run(track=False)

    assert not world.llm.contexts
    bill = world.bill(f"RCL/{project.id}")
    assert bill.status is BillStatus.DOCUMENT_AMBIGUOUS
    assert bill.analysis_attempts == 0


def test_self_amendment_is_read_separately_from_its_baseline() -> None:
    baseline = rcl_document(1, "Projekt ustawy.docx")
    amendment = rcl_document(2, "Autopoprawka.docx")
    project = rcl_project(
        stages=(rcl_stage(1, "Projekt", "reached", rcl_folder(1, "Projekt", baseline, amendment)),),
        consultation=None,
    )
    operations = "AUTOPOPRAWKA\n" + "W art. 1 wyrazy 30 dni zastępuje się wyrazami 60 dni.\n" * 8
    world = World(
        extractor=FakeTextExtractor(
            page_count=0, by_content={b"law": law(), b"amendment": operations}
        )
    )
    world.add_rcl_project(project)
    world.rcl.files.update({baseline.url: b"law", amendment.url: b"amendment"})

    world.run(track=False)

    record = world.bill(f"RCL/{project.id}").analysis
    assert record is not None and record.source_url == baseline.url
    assert "30 dni" in world.llm.contexts[0].text
    assert operations in world.llm.contexts[0].text
    assert "nie tekst jednolity" in world.llm.contexts[0].text
    assert any(part.role == "self_amendment" for part in record.selection.parts)


@pytest.mark.parametrize("neutral_title", [False, True])
def test_sejm_self_amendment_is_discovered_through_its_parent_process(neutral_title: bool) -> None:
    operations = (
        "AUTOPOPRAWKA\n" + "W art. 1 dodaje się: cudzoziemiec otrzymuje zezwolenie na pobyt.\n" * 8
    )
    baseline = (
        law()
        .replace("cudzoziemcach", "postępowaniu administracyjnym")
        .replace("Cudzoziemiec", "Wnioskodawca")
    )
    world = World(
        extractor=FakeTextExtractor(
            page_count=0, by_content={b"%PDF": baseline, b"amendment": operations}
        )
    )
    title = (
        "Projekt ustawy o postępowaniu administracyjnym"
        if neutral_title
        else "Projekt ustawy o cudzoziemcach"
    )
    world.add_bill("3039", title)
    url = "https://api.test/3039-A.pdf"
    world.gateway.prints["3039-A"] = PrintInfo(
        term=10,
        number="3039-A",
        title="Autopoprawka do projektu ustawy o cudzoziemcach",
        process_prints=("3039",),
        attachments=(Attachment(print_number="3039-A", name="3039-A.pdf", url=url),),
    )
    world.gateway.files[url] = b"amendment"

    report = world.run(track=False)

    record = world.bill("3039").analysis
    assert record is not None
    assert report.text_prefilter_hits == int(neutral_title)
    assert operations in world.llm.contexts[0].text
    assert any(part.url == url and part.role == "self_amendment" for part in record.selection.parts)


def test_missing_required_print_volume_waits_without_a_metadata_analysis() -> None:
    world = World(extractor=FakeTextExtractor(law(), page_count=0))
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    first = "https://api.test/3039c1.pdf"
    second = "https://api.test/3039c2.pdf"
    world.gateway.prints["3039"] = world.gateway.prints["3039"].model_copy(
        update={
            "attachments": tuple(
                Attachment(print_number="3039", name=name, url=url)
                for name, url in (("3039c1.pdf", first), ("3039c2.pdf", second))
            )
        }
    )
    world.gateway.files[first] = b"%PDF"

    world.run(track=False)

    assert not world.llm.contexts
    assert world.bill("3039").status is BillStatus.ANALYSIS_PENDING
    assert world.bill("3039").analysis_attempts == 0


@pytest.mark.parametrize("kind", ["committee_report", "text_after3"])
def test_a_fresh_process_revision_rechecks_an_unchanged_document_url(kind: str) -> None:
    world = World()
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run()
    bill = world.bill("3039")
    assert bill.analysis is not None
    checked = bill.analysis.created_at
    detail = world.gateway.get_process(10, "3039")
    stage = detail.stages[0].model_copy(
        update={
            "text_after3": bill.analysis.source_url if kind == "text_after3" else None,
            "report_file": bill.analysis.source_url if kind == "committee_report" else None,
            "stage_type": "CommitteeReport",
            "proposal": "załączony projekt ustawy",
        }
    )
    detail = detail.model_copy(
        update={"stages": (stage,), "change_date": checked + dt.timedelta(days=1)}
    )

    document = SejmTextSource(world.gateway).newer(bill, detail, None)

    assert document is not None and document.url == bill.analysis.source_url
    assert document.kind == kind


def test_legacy_documents_do_not_acquire_verified_quality_on_restore() -> None:
    old = TextDocument.model_validate({"url": "https://example.test/bill.pdf", "kind": "print"})

    assert old.alternatives == () and old.amendment_urls == ()


def test_numeric_rcl_draft_dates_select_the_actual_new_text() -> None:
    old = rcl_document(1, "Projekt ustawy.docx")
    new = rcl_document(2, "Projekt ustawy NOWY TEKST.docx")
    before = law().replace("1 września 2026", "02.06.2026")
    after = law(days=60).replace("1 września 2026", "10.08.2026")
    project = rcl_project(
        stages=(rcl_stage(1, "Projekt", "reached", rcl_folder(1, "Projekt", old, new)),),
        consultation=None,
    )
    world = World(
        extractor=FakeTextExtractor(page_count=0, by_content={b"old": before, b"new": after})
    )
    world.add_rcl_project(project)
    world.rcl.files.update({old.url: b"old", new.url: b"new"})

    world.run(track=False)

    record = world.bill(f"RCL/{project.id}").analysis
    assert record is not None and record.source_url == new.url
    assert world.llm.contexts[0].text == after

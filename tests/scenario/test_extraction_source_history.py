import io
import threading
from datetime import datetime
from typing import Literal

import pytest
from pypdf import PdfWriter

from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.document_text import DocumentTextExtractor, DocxTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models import ExtractionResult, ReviewSource, TextDocument
from tests.harness import World, print_url
from tests.unit.test_extraction_results import TEXT
from tests.unit.test_physical_extraction import docx


def reader() -> DocumentTextExtractor:
    return DocumentTextExtractor(PypdfTextExtractor(), DocxTextExtractor(), DocTextExtractor())


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("prefilter", [False, True])
def test_runtime_extractions_are_owned_by_source_transactions_on_the_calling_thread(
    workers: int, prefilter: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(workers=workers, extractor=reader())
    for number in ("4100", "4101", "4102"):
        title = "Projekt ustawy o podatku" if prefilter else "Projekt ustawy o cudzoziemcach"
        world.add_bill(number, title)
        world.gateway.files[print_url(number)] = docx(TEXT)
    caller = threading.get_ident()
    threads: list[int] = []
    save = world.repo.save_extraction_result

    def record(result: ExtractionResult, *, recorded_at: datetime) -> None:
        threads.append(threading.get_ident())
        save(result, recorded_at=recorded_at)

    monkeypatch.setattr(world.repo, "save_extraction_result", record)

    report = world.run(track=False)
    world.repo.restore(world.repo.dump())

    assert not report.errors
    assert threads and set(threads) == {caller}
    for number in ("4100", "4101", "4102"):
        observations = world.repo.list_source_observations(10, number)
        assert observations
        assert ("prefilter" in {source.purpose for source in observations}) == prefilter
        for source in observations:
            links = world.repo.list_selection_extractions(source.selection_id)
            assert len(links) == 1
            result = world.repo.get_extraction_result(links[0].extraction_id)
            assert result is not None, "the source transaction stores its extraction result"
            assert result.selected_parts[0].coordinates == "word-blocks-v1"
            assert result.bodies == "not_retained"


@pytest.mark.parametrize("purpose", ["review", "amendments", "supplement"])
def test_review_and_secondary_reads_share_the_runtime_extraction_history(
    purpose: Literal["review", "amendments", "supplement"],
) -> None:
    world = World(
        extractor=reader(),
        consultation_reviews_enabled=True,
        batch_kinds=frozenset({"analysis", "reanalysis", "consultation_review"}),
    )
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.gateway.files[print_url("4100")] = docx(TEXT)
    world.run(track=False)
    document = TextDocument(url="https://api.test/secondary.docx", kind="government_position")
    world.gateway.files[document.url] = docx(TEXT + "Nowe źródło.")
    if purpose == "review":
        world.analysis.review_context(
            world.bill("4100"),
            "consultation_review",
            (ReviewSource(identity="response/1", role="response", document=document),),
        )
    elif purpose == "amendments":
        world.analysis.amendments_context(world.bill("4100"), document)
    else:
        world.analysis.digest_supplement(
            world.bill("4100"), document, number="4101", title="Stanowisko Rządu"
        )

    sources = [
        source
        for source in world.repo.list_source_observations(10, "4100")
        if source.document.url == document.url
    ]
    assert len(sources) == 1
    links = world.repo.list_selection_extractions(sources[0].selection_id)
    assert len(links) == 1
    assert world.repo.get_extraction_result(links[0].extraction_id) is not None


def test_scan_text_extraction_metadata_does_not_claim_provider_page_input() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    stream = io.BytesIO()
    writer.write(stream)
    world = World(extractor=reader())
    world.add_bill("4100", "Projekt ustawy o podatku")
    world.gateway.files[print_url("4100")] = stream.getvalue()

    report = world.run(track=False)

    assert report.text_prefilter_scans == 1
    sources = world.repo.list_source_observations(10, "4100")
    assert sources
    links = world.repo.list_selection_extractions(sources[0].selection_id)
    assert len(links) == 1
    result = world.repo.get_extraction_result(links[0].extraction_id)
    assert result is not None, "textless PDF extraction retains its physical observation"
    assert result.output_chars == 0 and result.selected_parts[0].block_count == 1
    assert result.selected_parts[0].coordinates == "pdf-pages-v1"
    assert result.input_mapping == "unmapped" and result.completeness == "not_asserted"


def test_failed_source_outcome_rolls_back_extraction_and_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(extractor=reader())
    world.add_bill("4100", "Projekt ustawy o podatku")
    world.gateway.files[print_url("4100")] = docx(TEXT)
    world.repo.upsert_summary(world.gateway.processes[0], now=world.clock.now())
    before = world.repo.dump()

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("status failed")

    monkeypatch.setattr(world.repo, "set_status", fail)
    prefilter = world.container.text_prefilter_service()
    assert prefilter is not None, "World enables text prefilter"

    with pytest.raises(RuntimeError, match="status failed"):
        prefilter.check(world.bill("4100"))

    assert world.repo.dump() == before

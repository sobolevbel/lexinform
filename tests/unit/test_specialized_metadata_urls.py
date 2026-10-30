import datetime as dt
import hashlib
import threading
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.concurrency import fan_out
from lexinform.models import (
    Attachment,
    ExtractionResult,
    PrintInfo,
    RclConsultation,
    Stage,
    TextDocument,
)
from lexinform.models.rcl_letter_use import RclLetterUrl, rcl_letter_sha256
from lexinform.sections import TextBudget
from lexinform.services.analysis import AnalysisOptions, AnalysisService
from lexinform.services.document_read_reporting import CliDocumentReadReporter
from lexinform.services.documents import TextLoader
from lexinform.services.rcl_letter_history import (
    RclLetterHistoryPersistence,
    record_rcl_letter_uses,
)
from lexinform.services.senate_documents import resolution_source_read
from lexinform.services.senate_history import SenateInspectionCollector
from lexinform.services.sources import SejmTextSource
from tests.fakes import FakeLlm, FakeSejmGateway, FixedClock
from tests.unit.test_physical_extraction import docx, extractor
from tests.unit.test_rcl_letter_capture import capture_reader, captured
from tests.unit.test_senate_inspection import NOW, SUBJECT, TEXT, URL, payload, repository


@pytest.mark.parametrize("suffix", ["%0A", "%25E2%2580%25AE", "\ud800"])
def test_rcl_intersection_and_unencodable_omission_keep_actual_body_extraction(
    repo: SqliteBillRepository, suffix: str
) -> None:
    url = "https://legislacja.rcl.gov.pl/letter" + suffix + ".docx"
    reader, project, observer, loader = capture_reader(url=url, physical=True)
    clock = FixedClock(NOW)
    reporter = CliDocumentReadReporter(
        repo,
        clock,
        loader,
        loader,
        loader,
        loader,
        dt.timedelta(days=90),
        RclLetterHistoryPersistence(observer),
    )

    with reporter.capture("analyze"):
        window = reader.consultation(project)

    assert window is not None and window.letter_url == url
    results = loader.read_extraction_results()
    assert len(results) == len(loader.read_artifacts()) == 1
    assert repo.get_extraction_result(results[0].result_id) == results[0]
    assert not repo.list_artifact_reads()
    diagnostic = loader.read_metadata_url_omissions()
    assert diagnostic is not None and diagnostic.total == 4
    if suffix == "\ud800":
        assert not observer.read_rcl_letter_uses()
        assert diagnostic.reasons[0].reason == "invalid_unicode"
        assert all(example.requested_url_sha256 is None for example in diagnostic.examples)
    else:
        value = captured(observer)
        wrapped = value.official.letter.url
        assert wrapped.value is None and wrapped.state == "omitted_unsafe"
        assert wrapped.sha256 == hashlib.sha256(url.encode()).hexdigest()
        assert RclLetterUrl.of(url).state == "complete"
        assert diagnostic.reasons[0].reason == "control_character"
    before = repo.dump()
    assert url not in before
    repo.restore(before)
    assert repo.dump() == before
    with repo.atomic():
        record_rcl_letter_uses(
            repo, observer.read_rcl_letter_uses(), recorded_at=NOW + dt.timedelta(days=365)
        )
    assert repo.dump() == before


def test_rcl_window_intersection_preserves_exact_observed_business_projection() -> None:
    reader, project, observer, loader = capture_reader(
        url="https://legislacja.rcl.gov.pl/letter.docx"
    )
    previous = RclConsultation(
        deadline=dt.date(2027, 1, 1),
        letter_url="https://legislacja.rcl.gov.pl/previous%0A.docx",
    )

    result = reader.consultation(project, known=previous)

    assert result is not None and result.letter_url == previous.letter_url
    value = captured(observer)
    assert value.before is not None and value.after is not None
    assert value.before.projection.letter_url is not None
    assert value.before.projection.letter_url.value is None
    assert value.after.projection.letter_url is not None
    assert value.after.projection.letter_url.value is None
    original = previous.model_dump(
        mode="json", include={"letter_url", "letter_date", "days", "deadline", "email"}
    )
    assert value.before.canonical_projection_sha256 == rcl_letter_sha256(original)
    diagnostic = loader.read_metadata_url_omissions()
    assert diagnostic is not None and diagnostic.total == 2


@pytest.mark.parametrize("workers", [1, 4])
def test_senate_unencodable_unattempted_descriptor_skips_fact_without_losing_physical_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workers: int
) -> None:
    repo = repository(tmp_path)
    owner = repo.get(10, "1929")
    assert owner is not None, "the repository fixture enrolls the Senate source owner"
    data = docx(escape(TEXT.replace("\f", "\n")))
    calls: list[str] = []

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        calls.append(url)
        return data

    loader = TextLoader({"api.sejm.gov.pl": download}, extractor(), max_bytes=5 * 1024 * 1024)
    collector = SenateInspectionCollector()
    clock = FixedClock(NOW)
    analysis = AnalysisService(
        repo,
        SejmTextSource(FakeSejmGateway()),
        loader,
        FakeLlm(),
        clock,
        AnalysisOptions(),
        text_budget=TextBudget(60000),
        senate_inspections=collector,
    )
    template = payload()
    info = PrintInfo(
        term=10,
        number="3213",
        title=template.context.print_title,
        attachments=(
            Attachment(print_number="3213", name="resolution.docx", url=URL),
            Attachment(print_number="foreign", name="unknown.bin", url=URL + "\ud800"),
        ),
    )
    stage = Stage.model_validate(template.context.stage.model_dump())
    caller = threading.get_ident()
    writes: list[int] = []
    save = repo.save_extraction_result

    def record(result: ExtractionResult, *, recorded_at: dt.datetime) -> None:
        writes.append(threading.get_ident())
        save(result, recorded_at=recorded_at)

    monkeypatch.setattr(repo, "save_extraction_result", record)
    reporter = CliDocumentReadReporter(
        repo, clock, loader, loader, loader, loader, dt.timedelta(days=90)
    )

    def resolve(_: int) -> TextDocument | None:
        return analysis.senate_amendments_document(info, stage, titles=(SUBJECT,), owner=owner)

    with reporter.capture("analyze"):
        for outcome in fan_out((0,), resolve, workers=workers):
            selected = outcome.result()
            assert selected is not None and selected.url == URL and selected.senate is not None
            assert selected.senate.sha256 == hashlib.sha256(data).hexdigest()

    assert not collector.snapshot() and calls == [URL] and writes == [caller]
    (result,) = loader.read_extraction_results()
    assert repo.get_extraction_result(result.result_id) == result
    diagnostic = loader.read_metadata_url_omissions()
    assert diagnostic is not None and diagnostic.total == 1
    assert diagnostic.reasons[0].reason == "invalid_unicode"
    assert diagnostic.examples[0].requested_url_sha256 is None
    before = repo.dump()
    repo.restore(before)
    assert repo.dump() == before
    repo.close()


@pytest.mark.parametrize("suffix", ["", "%0A", "?token=PRIVATE"])
def test_resolution_source_keeps_existing_configured_origin_contract_and_safe_return_type(
    repo: SqliteBillRepository, suffix: str
) -> None:
    url = "https://custom.test:9443/resolution.docx" + suffix
    data = docx("USTAWA\nArt. 1. Cudzoziemiec sklada wniosek. " * 30)

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        return data

    loader = TextLoader({"custom.test": download}, extractor(), max_bytes=len(data))
    document = TextDocument(url=url, kind="senate_amendments")
    clock = FixedClock(NOW)
    reporter = CliDocumentReadReporter(
        repo, clock, loader, loader, loader, loader, dt.timedelta(days=90)
    )
    with reporter.capture("analyze"):
        loaded = loader.read(url)
        source = resolution_source_read(
            loader, document, sha256=loaded.sha256, pages=loaded.pages, text=loaded.text
        )
    assert (source is None) == bool(suffix)
    assert len(loader.read_artifacts()) == len(loader.read_extraction_results()) == 1
    result = loader.read_extraction_results()[0]
    assert repo.get_extraction_result(result.result_id) == result

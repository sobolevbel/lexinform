import datetime as dt
import sqlite3
import threading

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.concurrency import fan_out
from lexinform.models import ExtractionResult, TextDocument
from lexinform.services.document_history import SourceReadCapture, record_source_read
from lexinform.services.document_read_reporting import CliDocumentReadReporter
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway, FixedClock
from tests.unit.test_physical_extraction import archive, docx, extractor

MAIN = "https://source.test/main.docx"
REQUIRED = "https://source.test/required.zip?token=PRIVATE"
TEXT = "USTAWA\nArt. 1. Cudzoziemiec sklada wniosek. " * 30


def reader() -> tuple[TextLoader, TextDocument, FakeSejmGateway]:
    files = {MAIN: docx(TEXT), REQUIRED: archive({})}
    gateway = FakeSejmGateway(files=files)
    loader = TextLoader({"source.test": gateway.download}, extractor(), max_bytes=100000)
    return loader, TextDocument(url=MAIN, kind="print", required_urls=(REQUIRED,)), gateway


def inventory_rows(repo: SqliteBillRepository) -> str:
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(repo.dump())
        return "\n".join(
            row
            for row in connection.iterdump()
            if row.startswith(('INSERT INTO "artifacts"', 'INSERT INTO "extraction_results"'))
        )


@pytest.mark.parametrize("workers", [1, 4])
def test_cli_finalizer_persists_known_extractions_after_blocked_required_composition(
    repo: SqliteBillRepository, monkeypatch: pytest.MonkeyPatch, workers: int
) -> None:
    loader, requested, gateway = reader()
    clock = FixedClock()
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

    def read(document: TextDocument) -> SourceReadCapture:
        resolved, file = loader.select_document(document)
        assert file.selection.state == "waiting" and not file.selection.parts
        return loader.capture_source_read(document, resolved, file)

    with reporter.capture("analyze"):
        for outcome in fan_out((requested,), read, workers=workers):
            with repo.atomic():
                record_source_read(
                    repo,
                    outcome.result(),
                    term=10,
                    number="1",
                    purpose="analysis",
                    recorded_at=clock.now(),
                )

    results = loader.read_extraction_results()
    assert len(results) == len(loader.read_artifacts()) == 2
    assert writes == [caller, caller]
    assert {result.artifact_sha256 for result in results} == {
        body.sha256 for body in loader.read_artifacts()
    }
    assert all(repo.get_extraction_result(result.result_id) == result for result in results)
    assert not repo.list_source_observations(10, "1")
    assert gateway.calls == [f"download:{MAIN}", f"download:{REQUIRED}"]
    first = inventory_rows(repo)
    dump = repo.dump()
    repo.restore(dump)
    assert repo.dump() == dump
    clock.advance(days=365)
    loader.reset_read_metrics()
    assert loader.read_extraction_results() == results
    with reporter.capture("analyze"):
        pass
    assert inventory_rows(repo) == first
    assert all(repo.get_extraction_result(result.result_id) == result for result in results)
    assert "PRIVATE" not in first


def test_failed_extraction_history_write_rolls_back_body_inventory_and_report(
    repo: SqliteBillRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    loader, requested, _ = reader()
    clock = FixedClock()
    reporter = CliDocumentReadReporter(
        repo, clock, loader, loader, loader, loader, dt.timedelta(days=90)
    )
    original = repo.save_extraction_result
    writes = 0

    def fail_second(result: ExtractionResult, *, recorded_at: dt.datetime) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("history write failed")
        original(result, recorded_at=recorded_at)

    monkeypatch.setattr(repo, "save_extraction_result", fail_second)

    with reporter.capture("scan"):
        loader.select_document(requested)

    assert writes == 2 and inventory_rows(repo) == ""
    assert not repo.list_runs(since=clock.now() - dt.timedelta(days=1))
    assert len(loader.read_extraction_results()) == 2

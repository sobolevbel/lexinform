import datetime as dt
import threading
from dataclasses import replace

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.concurrency import fan_out
from lexinform.log_context import current_log_fields, log_context
from lexinform.models import ArtifactMetadata, DocumentReadMetrics, RunMode, RunReport
from lexinform.ports import BillRepository
from lexinform.services import document_read_reporting
from lexinform.services.document_read_reporting import CliDocumentReadReporter
from lexinform.services.documents import TextLoader
from tests.fakes import FakeTextExtractor, FixedClock

URL = "https://source.test/original.pdf"
BODY = b"complete original"


def download_body(url: str, *, max_bytes: int | None = None) -> bytes:
    return BODY


def reporter(
    repo: SqliteBillRepository, reader: TextLoader, clock: FixedClock
) -> CliDocumentReadReporter:
    return CliDocumentReadReporter(
        repo, clock, reader, reader, reader, reader, dt.timedelta(days=90)
    )


class OpaqueHistory:
    def __init__(self, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[int, dt.datetime]] = []

    def persist_source_history(self, repo: BillRepository, *, recorded_at: dt.datetime) -> None:
        self.calls.append((threading.get_ident(), recorded_at))
        assert repo.list_artifact_reads(), (
            "The owner persists global receipts before its opaque hook"
        )
        repo.save_artifact_metadata(
            ArtifactMetadata(sha256="e" * 64, byte_size=17, recorded_at=recorded_at)
        )
        if self.failure is not None:
            raise self.failure


@pytest.mark.parametrize(
    "business_failure", [None, RuntimeError("business failure"), SystemExit(0), KeyboardInterrupt()]
)
def test_opaque_hook_failure_rolls_back_history_and_preserves_original_outcome(
    repo: SqliteBillRepository,
    monkeypatch: pytest.MonkeyPatch,
    business_failure: BaseException | None,
) -> None:
    clock = FixedClock()
    reader = TextLoader({"source.test": download_body}, FakeTextExtractor(), max_bytes=1000)
    history = OpaqueHistory(KeyboardInterrupt("private history error"))
    capture = replace(reporter(repo, reader, clock), source_history=history)

    def failed_log(*args: object, **kwargs: object) -> None:
        raise RuntimeError("logging failed")

    monkeypatch.setattr(document_read_reporting.log, "warning", failed_log)
    try:
        with capture.capture("scan"):
            reader.read(URL)
            if business_failure is not None:
                raise business_failure
    except BaseException as exc:
        assert exc is business_failure
    else:
        assert business_failure is None
    assert history.calls == [(threading.get_ident(), clock.now())]
    assert not repo.list_artifact_reads()
    assert repo.get_artifact_metadata("e" * 64) is None
    assert not saved(repo, clock)


def test_opaque_hook_commits_before_report_and_preserves_first_time(
    repo: SqliteBillRepository,
) -> None:
    clock = FixedClock()
    reader = TextLoader({"source.test": download_body}, FakeTextExtractor(), max_bytes=1000)
    history = OpaqueHistory()
    capture = replace(reporter(repo, reader, clock), source_history=history)
    with capture.capture("scan"):
        reader.read(URL)
    assert len(saved(repo, clock)) == 1
    record = repo.get_artifact_metadata("e" * 64)
    assert record is not None and record.recorded_at == clock.now(), (
        "The opaque hook commits its sentinel metadata before the completed report"
    )
    assert history.calls == [(threading.get_ident(), clock.now())]


def saved(repo: SqliteBillRepository, clock: FixedClock) -> list[RunReport]:
    return repo.list_runs(since=clock.now() - dt.timedelta(days=365))


@pytest.mark.parametrize("workers", [1, 4])
def test_caller_registers_before_workers_and_finishes_metadata_on_its_thread(
    repo: SqliteBillRepository, monkeypatch: pytest.MonkeyPatch, workers: int
) -> None:
    clock = FixedClock()
    caller = threading.get_ident()
    fields: list[dict[str, int | str]] = []
    writes: list[str] = []
    original_save = repo.save_artifact_metadata
    original_finish = repo.finish_run

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        fields.append(current_log_fields())
        return BODY

    def save(value: ArtifactMetadata) -> None:
        assert threading.get_ident() == caller
        writes.append("artifact")
        original_save(value)

    def finish(run_id: int, report: RunReport) -> None:
        assert threading.get_ident() == caller
        writes.append("report")
        original_finish(run_id, report)

    monkeypatch.setattr(repo, "save_artifact_metadata", save)
    monkeypatch.setattr(repo, "finish_run", finish)
    reader = TextLoader({"source.test": download}, FakeTextExtractor(), max_bytes=1000)
    with log_context(observation_id="outer"):
        with reporter(repo, reader, clock).capture("reprefilter"):
            assert not saved(repo, clock) and "'cli_reads'" in repo.dump()
            assert not repo.list_artifact_reads()
            for outcome in fan_out(
                [f"{URL}/part-{n}.pdf" for n in range(4)], reader.read, workers=workers
            ):
                outcome.result()
            clock.advance(seconds=5)
        assert current_log_fields() == {"observation_id": "outer"}

    (report,) = saved(repo, clock)
    assert report.mode is RunMode.CLI_READS and report.cli_read_operation == "reprefilter"
    assert (
        report.term is None and not report.discovery_ok and not report.execution_accounting_measured
    )
    assert report.duration_seconds == 5
    assert report.document_reads is not None, "The caller snapshots measured response counters"
    assert report.document_reads.completed == 4
    assert report.document_download_diagnostics is not None, "Workers performed loader invocations"
    assert report.document_download_diagnostics.completed == 4
    assert writes[-1] == "report" and len(repo.list_artifact_reads()) == 4
    assert len({value["run_id"] for value in fields}) == 1
    assert all(type(value["run_id"]) is int and "command_id" not in value for value in fields)
    assert current_log_fields() == {}


def test_reset_preserves_warm_cache_and_first_complete_receipt(
    repo: SqliteBillRepository,
) -> None:
    clock = FixedClock()
    calls: list[str] = []

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        calls.append(url)
        return BODY

    reader = TextLoader({"source.test": download}, FakeTextExtractor(), max_bytes=1000)
    capture = reporter(repo, reader, clock)
    with capture.capture("analyze"):
        reader.read(URL)
    receipts = repo.list_artifact_reads()
    clock.advance(days=1)
    with capture.capture("analyze"):
        reader.read(URL)
    warm = saved(repo, clock)[0]
    clock.advance(days=1)
    with capture.capture("analyze"):
        pass
    empty = saved(repo, clock)[0]

    assert calls == [URL] and repo.list_artifact_reads() == receipts
    assert warm.document_reads is not None, "The warm-cache call has measured read counters"
    assert warm.document_reads.completed == 0 and warm.document_reads.cache_hits == 1
    assert warm.document_download_diagnostics is not None, "A cache hit is an observed loader event"
    assert warm.document_download_diagnostics.invocations == 0
    assert warm.document_download_diagnostics.cache_hits == 1
    assert empty.is_empty and empty.document_download_diagnostics is None
    assert "document_download_diagnostics" not in empty.model_dump_json()


@pytest.mark.parametrize(
    "failure",
    [ValueError("SECRET body"), KeyboardInterrupt("SECRET"), SystemExit(0), SystemExit(2)],
)
def test_finally_preserves_caller_exception_identity_and_completed_body(
    repo: SqliteBillRepository, failure: BaseException
) -> None:
    clock = FixedClock()
    reader = TextLoader({"source.test": download_body}, FakeTextExtractor(), max_bytes=1000)
    with pytest.raises(type(failure)) as caught, reporter(repo, reader, clock).capture("explain"):
        reader.read(URL)
        raise failure

    assert caught.value is failure and current_log_fields() == {}
    (report,) = saved(repo, clock)
    assert report.document_download_diagnostics is not None, "Finally preserves completed IO"
    assert report.document_download_diagnostics.completed == 1
    assert len(repo.list_artifact_reads()) == 1
    assert "SECRET" not in report.model_dump_json() and not report.errors


def test_extraction_failure_has_a_complete_receipt_without_execution_status(
    repo: SqliteBillRepository,
) -> None:
    clock = FixedClock()
    error = ValueError("SECRET parser message")
    reader = TextLoader(
        {"source.test": download_body},
        FakeTextExtractor(error=error),
        max_bytes=1000,
    )
    with pytest.raises(ValueError) as caught, reporter(repo, reader, clock).capture("scan"):
        reader.read(URL)

    assert caught.value is error
    (report,) = saved(repo, clock)
    assert report.document_download_diagnostics is not None, "The parser failed after download"
    assert report.document_download_diagnostics.completed == 1
    assert report.document_download_diagnostics.error == 0
    assert repo.list_artifact_reads() and "SECRET" not in report.model_dump_json()


def test_finalization_is_atomic_and_keeps_the_registered_row_unfinished_on_failure(
    repo: SqliteBillRepository, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FixedClock()
    old = RunReport(
        started_at=clock.now() - dt.timedelta(days=100), since=clock.now(), mode=RunMode.COMMANDS
    )
    repo.finish_run(repo.start_run(old), old)
    reader = TextLoader({"source.test": download_body}, FakeTextExtractor(), max_bytes=1000)
    original_prune = repo.prune_runs

    def prune(*, before: dt.datetime) -> int:
        original_prune(before=before)
        raise OSError("SECRET diagnostic failure")

    monkeypatch.setattr(repo, "prune_runs", prune)
    with reporter(repo, reader, clock).capture("scan"):
        reader.read(URL)
    assert saved(repo, clock) == [old]
    assert not repo.list_artifact_reads()
    assert repo.get_artifact_metadata(reader.read_artifacts()[0].sha256) is None
    assert "CLI document-read capture failed (OSError)" in caplog.text
    assert "SECRET" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_outer_rollback_removes_registration_receipts_and_report(
    repo: SqliteBillRepository,
) -> None:
    clock = FixedClock()
    before = repo.dump()
    reader = TextLoader({"source.test": download_body}, FakeTextExtractor(), max_bytes=1000)
    repo.begin()
    with reporter(repo, reader, clock).capture("amendments"):
        reader.read(URL)
    assert saved(repo, clock) and repo.list_artifact_reads()
    repo.rollback()
    assert repo.dump() == before


@pytest.mark.parametrize("caller_fails", [False, True])
def test_snapshot_and_logging_failure_cannot_replace_the_caller_outcome(
    repo: SqliteBillRepository, monkeypatch: pytest.MonkeyPatch, caller_fails: bool
) -> None:
    clock = FixedClock()
    reader = TextLoader({"source.test": download_body}, FakeTextExtractor(), max_bytes=1000)
    error = KeyboardInterrupt("SECRET caller")

    def snapshot() -> DocumentReadMetrics:
        raise OSError("SECRET snapshot")

    def warning(*args: object) -> None:
        raise RuntimeError("SECRET log handler")

    monkeypatch.setattr(reader, "read_metrics", snapshot)
    monkeypatch.setattr(document_read_reporting.log, "warning", warning)

    def call() -> str:
        with reporter(repo, reader, clock).capture("reset"):
            if caller_fails:
                raise error
            return "original result"

    if caller_fails:
        with pytest.raises(KeyboardInterrupt) as caught:
            call()
        assert caught.value is error
    else:
        assert call() == "original result"
    assert not saved(repo, clock) and current_log_fields() == {}


def test_registration_failure_stops_before_work(
    repo: SqliteBillRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FixedClock()
    reader = TextLoader({"source.test": download_body}, FakeTextExtractor(), max_bytes=1000)
    worked: list[bool] = []

    def start(report: RunReport) -> int:
        raise OSError("registration failed")

    monkeypatch.setattr(repo, "start_run", start)
    with (
        pytest.raises(OSError, match="registration failed"),
        reporter(repo, reader, clock).capture("forget"),
    ):
        worked.append(True)
    assert not worked and current_log_fields() == {}

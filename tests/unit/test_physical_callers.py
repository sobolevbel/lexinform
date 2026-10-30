import hashlib
import threading
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from typing import NoReturn

import pytest

from lexinform import physical_observations
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.concurrency import fan_out
from lexinform.models.physical_inventory import PhysicalCapture, PhysicalMetadata
from lexinform.physical_observations import PhysicalObservationCollector
from lexinform.physical_root_assembly import PhysicalAssemblyPacket
from lexinform.physical_walk_capture import PhysicalWalkCapture
from lexinform.services.document_read_reporting import CliDocumentReadReporter
from lexinform.services.documents import LoadedFile, TextLoader
from lexinform.services.physical_history import PhysicalHistoryPersistence
from lexinform.services.source_omissions import SourceOmissionInput
from tests.fakes import FakeSejmGateway, FakeTextExtractor, FixedClock
from tests.unit.test_document_text import _docx
from tests.unit.test_physical_walk_capture import extractor
from tests.unit.test_source_omission_capture import OWNER

NOW = datetime(2026, 10, 30, tzinfo=UTC)
URL = "https://source.test/document"


def loader_for(data: bytes) -> tuple[TextLoader, FakeSejmGateway]:
    gateway = FakeSejmGateway(files={URL: data})
    loader = TextLoader(
        {"source.test": gateway.download},
        extractor(),
        max_bytes=100000,
        physical_factory=PhysicalObservationCollector,
    )
    return loader, gateway


def test_loader_cache_reuses_original_capture_across_resets_without_new_walk_or_io(
    repo: SqliteBillRepository,
) -> None:
    data = _docx("<w:p><w:r><w:t>" + "USTAWA " * 50 + "</w:t></w:r></w:p>")
    loader, gateway = loader_for(data)
    first = loader.read(URL)
    (entry,) = loader.read_physical_observations()
    packet = entry.observation.assembly
    assert isinstance(packet, PhysicalAssemblyPacket), (
        "the actual cooperative invocation seals its physical roots"
    )
    assert entry.observation.artifact is not None, (
        "the downloaded complete body is known before extraction"
    )
    assert entry.observation.artifact.sha256 == hashlib.sha256(data).hexdigest()
    PhysicalHistoryPersistence(loader).persist_source_history(repo, recorded_at=NOW)
    before = repo.dump()
    loader.reset_read_metrics()
    loader.reset_download_diagnostics()
    assert loader.read(URL) == first
    (cached,) = loader.read_physical_observations()
    assert cached.reused and cached.observation is entry.observation
    metrics = loader.read_physical_diagnostics()
    assert metrics is not None, (
        "cached original evidence is measured independently from new invocations"
    )
    assert metrics.invocations == 0 and metrics.cache_reuses == 1
    PhysicalHistoryPersistence(loader).persist_source_history(
        repo, recorded_at=NOW.replace(microsecond=1)
    )
    assert repo.dump() == before
    saved = repo.get_physical_capture(packet.capture.capture_id)
    assert saved is not None, "caller persistence writes the actual matching root closure"
    repo.restore(before)
    assert repo.dump() == before
    assert gateway.calls == [f"download:{URL}"]


def test_noncooperative_plain_extractor_keeps_known_root_and_explicit_unavailability(
    repo: SqliteBillRepository,
) -> None:
    data = b"actual body"
    gateway = FakeSejmGateway(files={URL: data})
    loader = TextLoader(
        {"source.test": gateway.download},
        FakeTextExtractor("USTAWA " * 50),
        max_bytes=100000,
        physical_factory=PhysicalObservationCollector,
    )
    assert loader.load(URL) == "USTAWA " * 50
    (entry,) = loader.read_physical_observations()
    assert entry.observation.artifact is not None, (
        "download identity exists independently of custom hooks"
    )
    assert entry.observation.artifact.sha256 == hashlib.sha256(data).hexdigest()
    assert entry.observation.assembly is None
    assert entry.observation.unavailable == "cooperative_invocation_unavailable"
    PhysicalHistoryPersistence(loader).persist_source_history(repo, recorded_at=NOW)
    assert not list(repo.iter_physical_captures())
    metrics = loader.read_physical_diagnostics()
    assert metrics is not None, "the caller can report unknown custom extraction scope"
    assert metrics.invocations == metrics.unavailable == 1


def test_caller_transaction_rolls_back_physical_rows_and_parents_together(
    repo: SqliteBillRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader, _ = loader_for(_docx("<w:p/>"))
    loaded = loader.read(URL)
    before = repo.dump()
    original = repo.save_physical_inventory

    def broken(
        metadata: tuple[PhysicalMetadata, ...] = (),
        captures: tuple[PhysicalCapture, ...] = (),
        *,
        recorded_at: datetime,
    ) -> None:
        original(metadata, captures, recorded_at=recorded_at)
        raise RuntimeError("injected physical write")

    monkeypatch.setattr(repo, "save_physical_inventory", broken)
    with pytest.raises(RuntimeError, match="injected physical write"):
        PhysicalHistoryPersistence(loader).persist_source_history(repo, recorded_at=NOW)
    assert repo.dump() == before
    assert loaded.sha256 is not None


def test_completed_but_unyielded_worker_observations_survive_fanout_close() -> None:
    loader, gateway = loader_for(_docx("<w:p/>"))
    slots = loader.source_omission_inputs((OWNER, OWNER))
    done = threading.Event()
    failure = RuntimeError("stop consumer")

    def read(item: int) -> None:
        if item == 0:
            assert done.wait(5), "the other worker completes before the consumer aborts"
            raise failure
        loader.read(URL)
        done.set()

    with (
        pytest.raises(RuntimeError) as raised,
        closing(
            fan_out(
                tuple(enumerate(slots)),
                lambda item: loader.run_source_omission_input(item[1], read, item[0]),
                workers=2,
            )
        ) as outcomes,
    ):
        for outcome in outcomes:
            outcome.result()
    assert raised.value is failure
    (entry,) = loader.read_physical_observations()
    assert entry.order[1] == 1
    assert isinstance(entry.observation.assembly, PhysicalAssemblyPacket), (
        "started unconsumed work seals on its worker finally"
    )
    assert gateway.calls == [f"download:{URL}"]


def test_bounded_transport_keeps_same_input_prefix_in_one_and_four_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(physical_observations, "MAX_PENDING_PHYSICAL_OBSERVATIONS", 1)
    snapshots: list[tuple[tuple[int, ...], ...]] = []
    for workers in (1, 4):
        urls = tuple(f"https://source.test/{index}" for index in range(4))
        gateway = FakeSejmGateway(files={url: _docx("<w:p/>") for url in urls})
        loader = TextLoader(
            {"source.test": gateway.download},
            extractor(),
            max_bytes=100000,
            physical_factory=PhysicalObservationCollector,
        )
        slots = loader.source_omission_inputs((OWNER,) * 4)

        def read(
            item: tuple[str, SourceOmissionInput | None], reader: TextLoader = loader
        ) -> LoadedFile:
            return reader.run_source_omission_input(item[1], reader.read, item[0])

        with closing(
            fan_out(tuple(zip(urls, slots, strict=True)), read, workers=workers)
        ) as outcomes:
            for outcome in outcomes:
                outcome.result()
        entries = loader.read_physical_observations()
        snapshots.append(tuple(entry.order for entry in entries))
        metrics = loader.read_physical_diagnostics()
        assert metrics is not None, "transport overflow has an explicit diagnostic"
        assert metrics.invocations == 4 and metrics.dropped == 3 and metrics.unknown_remainders
    assert snapshots[0] == snapshots[1]
    assert snapshots[0][0][1] == 0


@pytest.mark.parametrize("business_failure", [False, True])
def test_observer_setup_and_failure_diagnostics_cannot_replace_business_result(
    monkeypatch: pytest.MonkeyPatch, business_failure: bool
) -> None:
    cause = LookupError("original cause")
    failure = ValueError("original extraction")
    failure.__cause__ = cause
    gateway = FakeSejmGateway(files={URL: b"body"})
    loader = TextLoader(
        {"source.test": gateway.download},
        FakeTextExtractor("USTAWA " * 50, error=failure if business_failure else None),
        max_bytes=100000,
        physical_factory=PhysicalObservationCollector,
    )

    def broken(*args: object, **kwargs: object) -> NoReturn:
        raise KeyboardInterrupt("metadata only")

    monkeypatch.setattr(PhysicalObservationCollector, "invocation", broken)
    monkeypatch.setattr(PhysicalObservationCollector, "note_failure", broken)
    if business_failure:
        with pytest.raises(ValueError) as raised:
            loader.read(URL)
        assert raised.value is failure and raised.value.__cause__ is cause
    else:
        assert loader.read(URL).text == "USTAWA " * 50
    assert gateway.calls == [f"download:{URL}"]
    diagnostics = loader.read_physical_diagnostics()
    assert diagnostics is not None, "failed optional callbacks leave explicit unknown coverage"
    assert diagnostics.unknown_remainders and diagnostics.observer_failures == 1


def test_failed_observer_reset_excludes_stale_pending_without_discarding_cache_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader, gateway = loader_for(_docx("<w:p/>"))
    loaded = loader.read(URL)
    (original,) = loader.read_physical_observations()

    def broken(*args: object, **kwargs: object) -> NoReturn:
        raise RuntimeError("metadata reset")

    with monkeypatch.context() as patch:
        patch.setattr(PhysicalObservationCollector, "reset", broken)
        patch.setattr(PhysicalObservationCollector, "note_failure", broken)
        loader.reset_read_metrics()
        assert loader.read_physical_observations() == ()
        assert loader.read(URL) == loaded
        assert loader.read_physical_observations() == ()
        diagnostics = loader.read_physical_diagnostics()
        assert diagnostics is not None, "reset failure suspends telemetry for this run"
        assert diagnostics.unknown_remainders and diagnostics.invocations == 0
    loader.reset_read_metrics()
    assert loader.read(URL) == loaded
    (recovered,) = loader.read_physical_observations()
    assert recovered.reused and recovered.observation is original.observation
    assert gateway.calls == [f"download:{URL}"]


def test_cached_alias_uses_earliest_actual_input_even_when_it_completes_last() -> None:
    snapshots: list[tuple[int, ...]] = []
    for workers in (1, 4):
        loader, gateway = loader_for(_docx("<w:p/>"))
        expected = loader.read(URL)
        (original,) = loader.read_physical_observations()
        loader.reset_read_metrics()
        slots = loader.source_omission_inputs((OWNER, OWNER))
        later_done = threading.Event()

        def read(
            index: int,
            reader: TextLoader = loader,
            done: threading.Event = later_done,
            concurrency: int = workers,
        ) -> LoadedFile:
            if index == 0 and concurrency == 4:
                assert done.wait(5), "the later cache hit completes before the first input"
            result = reader.read(URL)
            if index == 1:
                done.set()
            return result

        def run(
            item: tuple[int, SourceOmissionInput | None],
            reader: TextLoader = loader,
            step: Callable[[int], LoadedFile] = read,
        ) -> LoadedFile:
            return reader.run_source_omission_input(item[1], step, item[0])

        with closing(fan_out(tuple(enumerate(slots)), run, workers=workers)) as outcomes:
            assert [outcome.result() for outcome in outcomes] == [expected, expected]
        (entry,) = loader.read_physical_observations()
        snapshots.append(entry.order)
        assert entry.reused and entry.observation is original.observation
        assert gateway.calls == [f"download:{URL}"]
        diagnostics = loader.read_physical_diagnostics()
        assert diagnostics is not None, "cache reuse never claims a new physical execution"
        assert diagnostics.invocations == 0 and diagnostics.cache_reuses == 2
    assert snapshots[0] == snapshots[1]
    assert snapshots[0][1] == 0


def test_capture_context_receives_original_exception_and_cause() -> None:
    received: list[BaseException] = []

    class RecordingCapture(PhysicalWalkCapture):
        @contextmanager
        def scope(self) -> Iterator[PhysicalWalkCapture]:
            with super().scope():
                try:
                    yield self
                except BaseException as error:
                    received.append(error)
                    raise

    cause = LookupError("business cause")
    failure = ValueError("business extraction")
    failure.__cause__ = cause
    gateway = FakeSejmGateway(files={URL: b"body"})
    loader = TextLoader(
        {"source.test": gateway.download},
        FakeTextExtractor(error=failure),
        max_bytes=100000,
        physical_factory=lambda ordering: PhysicalObservationCollector(
            ordering, factory=RecordingCapture
        ),
    )
    with pytest.raises(ValueError) as raised:
        loader.read(URL)
    assert received == [failure]
    assert raised.value is failure and raised.value.__cause__ is cause
    assert gateway.calls == [f"download:{URL}"]


def test_capture_exit_failure_preserves_already_sealed_actual_facts() -> None:
    class FailingExitCapture(PhysicalWalkCapture):
        @contextmanager
        def scope(self) -> Iterator[PhysicalWalkCapture]:
            with super().scope():
                yield self
            raise KeyboardInterrupt("metadata cleanup")

    data = _docx("<w:p/>")
    gateway = FakeSejmGateway(files={URL: data})
    loader = TextLoader(
        {"source.test": gateway.download},
        extractor(),
        max_bytes=100000,
        physical_factory=lambda ordering: PhysicalObservationCollector(
            ordering, factory=FailingExitCapture
        ),
    )
    assert loader.read(URL).sha256 == hashlib.sha256(data).hexdigest()
    (entry,) = loader.read_physical_observations()
    assert isinstance(entry.observation.assembly, PhysicalAssemblyPacket), (
        "optional cleanup cannot erase actual facts already sealed by the real walk"
    )
    diagnostics = loader.read_physical_diagnostics()
    assert diagnostics is not None, "cleanup failure remains visible beside retained capture"
    assert diagnostics.captured == 1 and diagnostics.unavailable == 0
    assert diagnostics.observer_failures == 1 and diagnostics.unknown_remainders
    assert gateway.calls == [f"download:{URL}"]


@pytest.mark.parametrize("workers", [1, 4])
def test_cli_caller_persists_actual_capture_after_workers_and_cache_repeats_without_new_rows(
    repo: SqliteBillRepository, monkeypatch: pytest.MonkeyPatch, workers: int
) -> None:
    loader, gateway = loader_for(_docx("<w:p/>"))
    caller = threading.get_ident()
    original_save = repo.save_physical_inventory
    writes: list[int] = []

    def save(
        metadata: tuple[PhysicalMetadata, ...] = (),
        captures: tuple[PhysicalCapture, ...] = (),
        *,
        recorded_at: datetime,
    ) -> None:
        assert threading.get_ident() == caller
        writes.append(len(captures))
        original_save(metadata, captures, recorded_at=recorded_at)

    class CallerClock(FixedClock):
        def now(self) -> datetime:
            assert threading.get_ident() == caller
            return super().now()

    monkeypatch.setattr(repo, "save_physical_inventory", save)
    clock = CallerClock()
    reporter = CliDocumentReadReporter(
        repo,
        clock,
        loader,
        loader,
        loader,
        loader,
        timedelta(days=90),
        source_history=PhysicalHistoryPersistence(loader),
        physical=loader,
    )
    with reporter.capture("analyze"):
        slots = loader.source_omission_inputs((OWNER,))
        with closing(
            fan_out(
                slots,
                lambda slot: loader.run_source_omission_input(slot, loader.read, URL),
                workers=workers,
            )
        ) as outcomes:
            for outcome in outcomes:
                outcome.result()
        assert not list(repo.iter_physical_captures())
    (entry,) = loader.read_physical_observations()
    packet = entry.observation.assembly
    assert isinstance(packet, PhysicalAssemblyPacket), "native invocation yields a real graph"
    assert packet.capture.extraction_id is not None, "the caller binds the actual result identity"
    assert repo.get_extraction_result(packet.capture.extraction_id) is not None
    capture = repo.get_physical_capture(packet.capture.capture_id)
    metadata = tuple(repo.get_physical_metadata(row.metadata_id) for row in packet.metadata)
    assert capture is not None and all(row is not None for row in metadata)
    clock.advance(days=1)
    with reporter.capture("analyze"):
        loader.read(URL)
    assert repo.get_physical_capture(packet.capture.capture_id) == capture
    assert tuple(repo.get_physical_metadata(row.metadata_id) for row in packet.metadata) == metadata
    assert writes == [1, 1]
    assert gateway.calls == [f"download:{URL}"]
    restored = repo.dump()
    repo.restore(restored)
    assert repo.dump() == restored
    reports = repo.list_runs(since=clock.now() - timedelta(days=10))
    assert len(reports) == 2
    assert all(report.document_physical_diagnostics is not None for report in reports)


def test_final_report_exposes_optional_snapshot_failure_without_losing_business_receipt(
    repo: SqliteBillRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    loader, gateway = loader_for(_docx("<w:p/>"))
    clock = FixedClock()
    reporter = CliDocumentReadReporter(
        repo,
        clock,
        loader,
        loader,
        loader,
        loader,
        timedelta(days=90),
        source_history=PhysicalHistoryPersistence(loader),
        physical=loader,
    )

    def broken(*args: object, **kwargs: object) -> NoReturn:
        raise RuntimeError("optional snapshot")

    monkeypatch.setattr(PhysicalObservationCollector, "snapshot", broken)
    with reporter.capture("analyze"):
        assert loader.read(URL).sha256 is not None
    (report,) = repo.list_runs(since=clock.now() - timedelta(days=1))
    diagnostics = report.document_physical_diagnostics
    assert diagnostics is not None, "the report is finalized after optional history snapshots"
    assert diagnostics.observer_failures == 1 and diagnostics.unknown_remainders
    assert repo.list_artifact_reads() and not list(repo.iter_physical_captures())
    assert gateway.calls == [f"download:{URL}"]

import datetime as dt

import pytest

from lexinform.models import RunMode, RunReport
from tests.fakes import FakeTextExtractor
from tests.harness import World


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("scanned", [False, True])
def test_run_records_actual_download_bodies_and_restores_its_metrics(
    workers: int, scanned: bool
) -> None:
    world = World(
        workers=workers,
        extractor=FakeTextExtractor("", page_count=12) if scanned else FakeTextExtractor(),
    )
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")

    report = world.run(track=False)
    world.repo.restore(world.repo.dump())

    metrics = report.document_reads
    assert metrics is not None, "Container injects the shared TextLoader monitor into the pipeline"
    downloads = [
        call.removeprefix("download:")
        for call in world.gateway.calls
        if call.startswith("download:")
    ]
    assert metrics.completed == len(downloads)
    assert metrics.body_bytes == sum(len(world.gateway.files[url]) for url in downloads)
    assert metrics.distinct_body_bytes == sum(
        len(body) for body in {world.gateway.files[url] for url in downloads}
    )
    assert world.repo.list_runs(since=report.started_at)[0].document_reads == metrics


def test_each_run_starts_a_new_observation_without_replaying_old_downloads() -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    first = world.run(track=False)
    assert first.document_reads is not None and first.document_reads.completed > 0

    second = world.run(track=False)

    assert second.document_reads is not None, "a quiet run still has an observed empty I/O scope"
    assert second.document_reads.completed == second.document_reads.body_bytes == 0
    assert second.document_reads.distinct_bodies == second.document_reads.distinct_body_bytes == 0


def test_failed_run_retains_attempts_without_guessing_the_missing_body_size() -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.gateway.outages.add("download")

    report = world.run(track=False)

    assert report.errors
    assert report.document_reads is not None, "the pipeline collects observations in finally"
    assert sum(host.failed for host in report.document_reads.hosts.values()) == 1
    assert report.document_reads.body_bytes == report.document_reads.distinct_body_bytes == 0


def test_legacy_report_does_not_acquire_a_false_zero_download_measurement() -> None:
    now = dt.datetime(2026, 10, 27, tzinfo=dt.UTC)
    original = RunReport(started_at=now, since=now, mode=RunMode.RUN)

    restored = RunReport.model_validate_json(original.model_dump_json())

    assert restored.document_reads is None
    assert "document_reads" not in restored.model_dump_json()


def test_dry_run_returns_observed_io_while_rolling_back_its_database_changes() -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    before = world.repo.dump()

    report = world.run(track=False, dry_run=True)

    assert report.document_reads is not None, "dry run still observes its actual file reads"
    assert report.document_reads.completed > 0
    assert world.repo.dump() == before

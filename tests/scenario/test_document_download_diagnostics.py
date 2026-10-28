import threading

import pytest

from lexinform.models import RunReport
from tests.fakes import FakeTextExtractor
from tests.harness import World


@pytest.mark.parametrize("workers", [1, 4])
def test_pipeline_records_bounded_diagnostics_through_caller_finish_only(
    workers: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(workers=workers)
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    caller = threading.get_ident()
    finished: list[RunReport] = []
    original = world.repo.finish_run

    def finish(run_id: int, report: RunReport) -> None:
        assert threading.get_ident() == caller
        finished.append(report)
        original(run_id, report)

    monkeypatch.setattr(world.repo, "finish_run", finish)
    report = world.run(track=False)
    observed = report.document_download_diagnostics
    metrics = report.document_reads
    assert observed is not None and metrics is not None, "Container wires both independent monitors"
    assert observed.completed == metrics.completed and observed.cache_hits == metrics.cache_hits
    assert observed.invocations == sum(host.attempts for host in metrics.hosts.values())
    assert observed.completed > 0 and not observed.examples
    assert len(observed.model_dump_json().encode()) <= 2048
    assert len(finished) == 1 and finished[0] is report
    world.repo.restore(world.repo.dump())
    assert (
        world.repo.list_runs(since=report.started_at)[0].document_download_diagnostics == observed
    )


def test_empty_quiet_run_omits_diagnostics_without_replaying_old_events() -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    first = world.run(track=False)
    assert first.document_download_diagnostics is not None, "the first run reads its file"
    second = world.run(track=False)
    assert second.document_download_diagnostics is None
    assert "document_download_diagnostics" not in second.model_dump_json()
    assert second.document_reads is not None and second.document_reads.completed == 0


def test_source_outage_remains_phase_fatal_without_burning_bill_attempts() -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.gateway.outages.add("download")
    report = world.run(track=False)
    observed = report.document_download_diagnostics
    assert observed is not None, "pipeline finally snapshots the failed invocation"
    assert observed.source_unavailable == observed.invocations == 1
    assert observed.examples[0].status_code is None
    assert observed.examples[0].reason == "other_error"
    assert report.errors and not world.llm.contexts
    assert world.bill("4100").analysis_attempts == 0
    assert not world.repo.list_artifact_reads()


def test_extraction_error_does_not_reclassify_a_completed_download() -> None:
    world = World(extractor=FakeTextExtractor(error=ValueError("unsupported")))
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    report = world.run(track=False)
    observed = report.document_download_diagnostics
    metrics = report.document_reads
    assert observed is not None and metrics is not None, "the file returns before extraction fails"
    assert observed.completed == metrics.completed > 0
    assert observed.error == 0 and not observed.examples
    assert world.repo.list_artifact_reads()


def test_dry_run_returns_observed_diagnostics_and_rolls_back_all_state() -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    before = world.repo.dump()
    report = world.run(track=False, dry_run=True)
    assert report.document_download_diagnostics is not None, "dry run still performs observed reads"
    assert report.document_download_diagnostics.completed > 0
    assert world.repo.dump() == before

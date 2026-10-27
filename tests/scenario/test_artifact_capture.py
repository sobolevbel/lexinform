import hashlib

import pytest

from lexinform.models import BillStatus
from tests.fakes import FakeTextExtractor
from tests.harness import World


@pytest.mark.parametrize("workers", [1, 4])
def test_prefilter_links_known_bodies_before_a_paid_result_exists(workers: int) -> None:
    world = World(
        workers=workers, extractor=FakeTextExtractor("Art. 1. Podatek wynosi 10 zł. " * 30)
    )
    world.add_bill("4100", "Projekt ustawy o podatku")

    world.run(track=False)
    world.repo.restore(world.repo.dump())

    assert world.bill("4100").status is BillStatus.SKIPPED_TEXT_PREFILTER
    observed = world.repo.list_source_observations(10, "4100")
    assert len(observed) == 1
    links = world.repo.list_selection_artifacts(observed[0].selection_id)
    assert links and links[0].part_scope == "parts"
    artifact = world.repo.get_artifact_metadata(links[0].artifact_sha256)
    assert artifact is not None, "the source checkpoint records the complete body before its ref"
    assert artifact.sha256 == hashlib.sha256(b"%PDF").hexdigest()
    assert artifact.byte_size == len(b"%PDF")
    assert not world.repo.list_computation_results(10, "4100")


@pytest.mark.parametrize("workers", [1, 4])
def test_pipeline_captures_title_hits_and_preserves_the_first_recording_time(workers: int) -> None:
    world = World(workers=workers)
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    sha = hashlib.sha256(b"%PDF").hexdigest()
    first = world.repo.get_artifact_metadata(sha)
    assert first is not None, "the pipeline captures completed bodies outside text prefilter too"
    assert not world.repo.list_source_observations(10, "4100")
    world.clock.advance(days=30)

    report = world.run(track=False)

    assert report.document_reads is not None and report.document_reads.completed == 0
    assert world.repo.get_artifact_metadata(sha) == first


def test_body_identity_survives_a_dry_run_and_a_warm_cache_without_another_download() -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    before = world.repo.dump()
    world.run(track=False, dry_run=True)
    assert world.repo.dump() == before
    calls = sum(call.startswith("download:") for call in world.gateway.calls)
    assert calls > 0, "the dry run observes the fixture's real downloader invocation"
    world.clock.advance(days=1)

    report = world.run(track=False)

    assert report.document_reads is not None and report.document_reads.completed == 0
    assert len([call for call in world.gateway.calls if call.startswith("download:")]) == calls
    artifact = world.repo.get_artifact_metadata(hashlib.sha256(b"%PDF").hexdigest())
    assert artifact is not None, "a counter reset preserves the known identity of cached input"
    assert artifact.byte_size == 4 and artifact.recorded_at == world.clock.now()


@pytest.mark.parametrize("outage", [False, True])
def test_completed_unreadable_body_and_failed_fetch_are_distinct(outage: bool) -> None:
    world = World(extractor=FakeTextExtractor(error=ValueError("unsupported")))
    world.add_bill("4100", "Projekt ustawy o podatku")
    if outage:
        world.gateway.outages.add("download")

    world.run(track=False)

    artifact = world.repo.get_artifact_metadata(hashlib.sha256(b"%PDF").hexdigest())
    assert (artifact is None) == outage
    if not outage:
        assert world.bill("4100").status is BillStatus.DOCUMENT_UNSUPPORTED
        observed = world.repo.list_source_observations(10, "4100")
        assert len(observed) == 1
        assert not world.repo.list_selection_artifacts(observed[0].selection_id)

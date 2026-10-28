import hashlib
import threading
from datetime import timedelta

import pytest

from lexinform.models import ArtifactReadReceipt
from tests.fakes import FakeTextExtractor
from tests.harness import World, print_url


@pytest.mark.parametrize("workers", [1, 4])
def test_pipeline_records_actual_requested_url_and_complete_body_on_the_caller(
    workers: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(workers=workers)
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    caller = threading.get_ident()
    threads: list[int] = []
    save = world.repo.save_artifact_read

    def record(receipt: ArtifactReadReceipt) -> None:
        threads.append(threading.get_ident())
        save(receipt)

    monkeypatch.setattr(world.repo, "save_artifact_read", record)

    world.run(track=False)
    world.repo.restore(world.repo.dump())

    reads = world.repo.list_artifact_reads()
    assert len(reads) == 1 and threads and set(threads) == {caller}
    assert reads[0].requested_url == print_url("4100")
    assert reads[0].sha256 == hashlib.sha256(b"%PDF").hexdigest() and reads[0].byte_size == 4
    assert reads[0].recorded_at == world.clock.now()
    first = reads[0]
    world.clock.advance(days=30)
    report = world.run(track=False)
    assert report.document_reads is not None and report.document_reads.completed == 0
    assert world.repo.get_artifact_read(first.read_id) == first


@pytest.mark.parametrize("outage", [False, True])
def test_parser_failure_does_not_erase_a_download_and_outage_creates_no_receipt(
    outage: bool,
) -> None:
    world = World(extractor=FakeTextExtractor(error=ValueError("parser failed")))
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    if outage:
        world.gateway.outages.add("download")

    world.run(track=False)

    reads = world.repo.list_artifact_reads()
    assert len(reads) == (0 if outage else 1)
    assert not world.llm.contexts
    if reads:
        assert reads[0].requested_url == print_url("4100")
        assert reads[0].sha256 == hashlib.sha256(b"%PDF").hexdigest()


def test_over_limit_response_has_no_complete_body_receipt() -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.gateway.files[print_url("4100")] = b"x" * 10_000_001

    report = world.run(track=False)

    assert report.document_reads is not None, "the pipeline records its bounded downloader outcomes"
    assert sum(host.over_limit for host in report.document_reads.hosts.values()) > 0
    assert not world.repo.list_artifact_reads()


def test_alias_and_changed_response_are_preserved_without_erasing_cached_source_identity() -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    alias = "https://api.test/alias.pdf"
    world.gateway.files[alias] = b"%PDF"
    world.container.text_loader().read(alias)
    world.gateway.files[alias] = b"%PDF-changed"
    assert world.container.text_loader().load_scan(alias, cover_letter=False) is not None

    world.run(track=False)

    old_sha = hashlib.sha256(b"%PDF").hexdigest()
    new_sha = hashlib.sha256(b"%PDF-changed").hexdigest()
    assert len(world.repo.list_artifact_reads(sha256=old_sha)) == 2
    assert {r.sha256 for r in world.repo.list_artifact_reads(requested_url=alias)} == {
        old_sha,
        new_sha,
    }
    assert world.repo.get_artifact_metadata(new_sha) is not None


def test_dry_run_rolls_back_receipts_and_warm_cache_retains_actual_download_identity() -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    before = world.repo.dump()
    world.run(track=False, dry_run=True)
    assert world.repo.dump() == before
    calls = tuple(world.gateway.calls)
    world.clock.advance(days=1)

    report = world.run(track=False)

    assert report.document_reads is not None and report.document_reads.completed == 0
    assert [c for c in world.gateway.calls if c.startswith("download:")] == [
        c for c in calls if c.startswith("download:")
    ]
    reads = world.repo.list_artifact_reads()
    assert len(reads) == 1 and reads[0].recorded_at == world.clock.now()


def test_standalone_primary_source_transaction_records_its_actual_read() -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.container.discovery_service().discover(10, world.clock.now() - timedelta(days=365))

    world.analysis.analyze_bill(world.bill("4100"))

    reads = world.repo.list_artifact_reads()
    assert len(reads) == 1 and reads[0].requested_url == print_url("4100")
    assert world.repo.get_artifact_metadata(reads[0].sha256) is not None

import hashlib
import threading
from datetime import timedelta

import pytest

from lexinform.errors import LlmUnavailableError
from lexinform.models import (
    BillStatus,
    CandidateDispositionTrace,
    DocumentSelectionRecord,
    SourceObservation,
    TextDocument,
)
from tests.fakes import FakeTextExtractor
from tests.harness import World, print_url


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("batch", [False, True])
def test_analysis_records_source_before_input_fitting_and_preserves_first_time(
    workers: int, batch: bool
) -> None:
    source = "USTAWA\nArt. 1. Cudzoziemiec składa wniosek osobiście.\n" * 250
    world = World(
        workers=workers,
        batch_only=batch,
        extractor=FakeTextExtractor(source),
        text_budget_chars=1000,
    )
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")

    world.run(track=False)
    world.repo.restore(world.repo.dump())

    observations = world.repo.list_source_observations(10, "4100")
    assert len(observations) == 1 and observations[0].purpose == "analysis"
    first = observations[0]
    selection = world.repo.get_document_selection(first.selection_id)
    assert selection is not None, "source observation retains its exact source decision"
    assert selection.selection.parts and not any(p.text for p in selection.selection.parts)
    traces = world.repo.list_candidate_disposition_traces(first.observation_id)
    assert not traces
    assert not any(span.stage == "analysis" for span in selection.selection.coverage)
    links = world.repo.list_selection_artifacts(first.selection_id)
    assert links and links[0].artifact_sha256 == hashlib.sha256(b"%PDF").hexdigest()
    if batch:
        world.batch.resolve()
    world.clock.advance(days=1)
    world.run(track=False)
    assert world.repo.list_source_observations(10, "4100") == [first]


@pytest.mark.parametrize("workers", [1, 4])
def test_analysis_source_writes_stay_on_the_calling_thread(
    workers: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(
        workers=workers,
        extractor=FakeTextExtractor(
            "USTAWA\n" + "Art. 1. Cudzoziemiec składa wniosek osobiście.\n" * 30
        ),
    )
    for number in ("4100", "4101", "4102"):
        world.add_bill(number, "Projekt ustawy o cudzoziemcach")
    caller = threading.get_ident()
    save = world.repo.save_source_observation
    save_trace = world.repo.save_candidate_disposition_trace
    threads: list[int] = []
    trace_threads: list[int] = []

    def record(selection: DocumentSelectionRecord, observation: SourceObservation) -> None:
        threads.append(threading.get_ident())
        save(selection, observation)

    monkeypatch.setattr(world.repo, "save_source_observation", record)

    def record_trace(observation_id: str, trace: CandidateDispositionTrace) -> None:
        trace_threads.append(threading.get_ident())
        save_trace(observation_id, trace)

    monkeypatch.setattr(world.repo, "save_candidate_disposition_trace", record_trace)

    world.run(track=False)

    assert threads == [caller] * 3
    assert not trace_threads
    assert all(
        len(world.repo.list_source_observations(10, n)) == 1 for n in ("4100", "4101", "4102")
    )


@pytest.mark.parametrize("outage", [False, True])
def test_completed_unsupported_selection_and_source_outage_remain_distinct(outage: bool) -> None:
    world = World(extractor=FakeTextExtractor(error=ValueError("unsupported")))
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    if outage:
        world.gateway.outages.add("download")

    report = world.run(track=False)

    observed = world.repo.list_source_observations(10, "4100")
    assert not observed
    assert not world.llm.contexts
    if not outage:
        assert world.bill("4100").status is BillStatus.DOCUMENT_UNSUPPORTED
        assert world.repo.list_artifact_reads()
        assert report.metadata_url_omissions is not None
        assert report.metadata_url_omissions.reasons[0].reason == "opaque_failure_prose"
    else:
        assert not world.repo.list_artifact_reads() and report.metadata_url_omissions is None


@pytest.mark.parametrize("failure", [RuntimeError("bad answer"), LlmUnavailableError("down")])
def test_paid_preparation_failure_keeps_completed_source_without_changing_failure_policy(
    failure: Exception,
) -> None:
    world = World(llm_script={"4100": failure})
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")

    report = world.run(track=False)

    assert len(world.repo.list_source_observations(10, "4100")) == 1
    assert world.bill("4100").analysis is None
    assert report.analysis_failures == (0 if isinstance(failure, LlmUnavailableError) else 1)


def test_quiet_reanalysis_records_new_source_without_replacing_the_paid_result() -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    bill = world.bill("4100")
    original = bill.analysis
    assert original is not None, "first run produces an analysis"
    document = TextDocument(url="https://api.test/reprinted.pdf", kind="print")
    world.gateway.files[document.url] = world.gateway.files[print_url("4100")]

    assert world.analysis.reanalyze_bill(bill, document) is None

    observations = world.repo.list_source_observations(10, "4100")
    assert {o.document.url for o in observations} == {print_url("4100"), document.url}
    current = world.bill("4100").analysis
    assert current is not None, "quiet source pointer update keeps the existing paid result"
    assert current.computation_id == original.computation_id
    assert current.request_sha256 == original.request_sha256
    assert current.analysis == original.analysis


def test_nontrivial_reanalysis_trace_links_selected_source_on_calling_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    requested = TextDocument(
        url="https://api.test/conflicting-link.pdf",
        kind="print",
        official_link_conflict=True,
        alternatives=(TextDocument(url=print_url("4100"), kind="print"),),
    )
    caller = threading.get_ident()
    save = world.repo.save_candidate_disposition_trace
    writes: list[int] = []

    def record(observation_id: str, trace: CandidateDispositionTrace) -> None:
        writes.append(threading.get_ident())
        save(observation_id, trace)

    monkeypatch.setattr(world.repo, "save_candidate_disposition_trace", record)

    assert world.analysis.reanalyze_bill(world.bill("4100"), requested) is None

    observations = world.repo.list_source_observations(10, "4100")
    traced = [
        (observation, trace)
        for observation in observations
        for trace in world.repo.list_candidate_disposition_traces(observation.observation_id)
        if trace.requested_document == requested
    ]
    assert len(traced) == 1 and traced[0][0].document.url == print_url("4100")
    assert [item.outcome for item in traced[0][1].dispositions] == [
        "official_conflict",
        "selected",
    ]
    assert writes == [caller]


def test_dry_run_rolls_back_analysis_source_metadata() -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    before = world.repo.dump()

    world.run(track=False, dry_run=True)

    assert world.repo.dump() == before


def test_standalone_analysis_records_known_body_without_pipeline_finalization() -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.container.discovery_service().discover(10, world.clock.now() - timedelta(days=365))

    world.analysis.analyze_bill(world.bill("4100"))

    observed = world.repo.list_source_observations(10, "4100")
    assert len(observed) == 1
    links = world.repo.list_selection_artifacts(observed[0].selection_id)
    assert links and world.repo.get_artifact_metadata(links[0].artifact_sha256) is not None


def test_scan_observation_describes_source_selection_without_provider_derivative() -> None:
    world = World(extractor=FakeTextExtractor("", page_count=12))
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")

    world.run(track=False)

    observed = world.repo.list_source_observations(10, "4100")
    assert len(observed) == 1
    selected = world.repo.get_document_selection(observed[0].selection_id)
    assert selected is not None, "scan has a separate source decision"
    assert selected.selection.scan_input is None
    assert world.repo.list_selection_artifacts(observed[0].selection_id)
    record = world.bill("4100").analysis
    assert record is not None and record.selection.scan_input is not None

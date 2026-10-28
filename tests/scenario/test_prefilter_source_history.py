import threading

import pytest

from lexinform.keywords import KeywordPrefilter
from lexinform.models import (
    Bill,
    BillStatus,
    DocumentSelectionRecord,
    LocatedText,
    ReviewSource,
    SourceObservation,
    TextDocument,
)
from lexinform.models.reviews import ReviewPurpose
from lexinform.services.documents import TextLoader
from lexinform.services.text_prefilter import TextPrefilterService
from tests.fakes import FakeTextExtractor
from tests.harness import World, print_url


class FixedTextSource:
    def __init__(self, document: TextDocument) -> None:
        self.document = document

    def locate(self, bill: Bill) -> LocatedText:
        return LocatedText(document=self.document)

    def supporting_documents(self, bill: Bill, purpose: ReviewPurpose) -> tuple[ReviewSource, ...]:
        return ()


@pytest.mark.parametrize("workers", [1, 4])
def test_rejected_source_decision_survives_without_a_paid_analysis(workers: int) -> None:
    world = World(
        workers=workers,
        extractor=FakeTextExtractor("USTAWA\n" + "Art. 1. Podatek wynosi 10 zł. " * 30),
    )
    world.add_bill("4100", "Projekt ustawy o podatku")

    report = world.run(track=False)
    world.repo.restore(world.repo.dump())

    assert report.text_prefilter_checked == 1 and report.analyzed == 0
    assert world.bill("4100").status is BillStatus.SKIPPED_TEXT_PREFILTER
    observed = world.repo.list_source_observations(10, "4100")
    assert len(observed) == 1 and observed[0].purpose == "prefilter"
    selected = world.repo.get_document_selection(observed[0].selection_id)
    assert selected is not None, "the atomic source observation retains its exact selection"
    assert selected.selection.policy_version == "document-selection-v1"
    assert selected.selection.parts and all(not part.text for part in selected.selection.parts)
    traces = world.repo.list_candidate_disposition_traces(observed[0].observation_id)
    assert not traces
    assert not world.repo.list_computation_results(10, "4100")
    assert not world.llm.contexts and not world.batch.submitted


def test_repeat_check_keeps_the_first_source_time_and_exact_dump() -> None:
    world = World(extractor=FakeTextExtractor("Art. 1. Podatek wynosi 10 zł. " * 30))
    world.add_bill("4100", "Projekt ustawy o podatku")
    world.run(track=False)
    first = world.repo.list_source_observations(10, "4100")[0]
    before = world.repo.dump()
    world.clock.advance(days=30)
    service = world.container.text_prefilter_service()
    assert service is not None, "World enables text prefilter by default"

    assert not service.check(world.bill("4100"))

    assert world.repo.dump() == before
    assert world.repo.get_source_observation(first.observation_id) == first
    assert first.recorded_at < world.clock.now()


def test_nontrivial_prefilter_fallback_persists_original_request_and_rejection() -> None:
    world = World(extractor=FakeTextExtractor("Art. 1. Podatek wynosi 10 zł. " * 30))
    world.add_bill("4100", "Projekt ustawy o podatku")
    world.repo.upsert_summary(world.gateway.processes[0], now=world.clock.now())
    selected = TextDocument(url=print_url("4100"), kind="print")
    requested = TextDocument(
        url="https://api.test/conflicting-link.pdf",
        kind="print",
        official_link_conflict=True,
        alternatives=(selected,),
    )
    reader = TextLoader({"api.test": world.gateway.download}, world.extractor, max_bytes=1000)
    service = TextPrefilterService(
        world.repo,
        FixedTextSource(requested),
        reader,
        KeywordPrefilter(),
        clock=world.clock,
    )

    assert not service.check(world.bill("4100"))

    observations = world.repo.list_source_observations(10, "4100")
    assert len(observations) == 1
    traces = world.repo.list_candidate_disposition_traces(observations[0].observation_id)
    assert len(traces) == 1 and traces[0].requested_document == requested
    assert [item.outcome for item in traces[0].dispositions] == [
        "official_conflict",
        "selected",
    ]
    assert world.bill("4100").status is BillStatus.SKIPPED_TEXT_PREFILTER


@pytest.mark.parametrize("workers", [1, 4])
def test_parallel_prefilter_writes_source_history_only_on_the_calling_thread(
    workers: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(
        workers=workers, extractor=FakeTextExtractor("Art. 1. Podatek wynosi 10 zł. " * 30)
    )
    for number in ("4100", "4101", "4102"):
        world.add_bill(number, "Projekt ustawy o podatku")
    calling_thread = threading.get_ident()
    save = world.repo.save_source_observation
    owners: list[str] = []

    def record(selection: DocumentSelectionRecord, observed: SourceObservation) -> None:
        assert threading.get_ident() == calling_thread
        owners.append(observed.number)
        save(selection, observed)

    monkeypatch.setattr(world.repo, "save_source_observation", record)

    world.run(track=False)

    assert owners == ["4100", "4101", "4102"]
    assert all(len(world.repo.list_source_observations(10, number)) == 1 for number in owners)


@pytest.mark.parametrize("scanned", [False, True])
def test_unreadable_and_scan_decisions_are_retained_without_inventing_paid_inputs(
    scanned: bool,
) -> None:
    world = World(extractor=FakeTextExtractor("", page_count=12 if scanned else 0))
    world.add_bill("4100", "Projekt ustawy o podatku")

    report = world.run(track=False)

    observed = world.repo.list_source_observations(10, "4100")
    assert [o.purpose for o in observed] == (
        ["prefilter", "analysis"] if scanned else ["prefilter"]
    )
    selected = world.repo.get_document_selection(observed[0].selection_id)
    assert selected is not None, "the prefilter stores metadata before deciding about a scan"
    assert selected.selection.scan_input is None
    assert {o.selection_id for o in observed} == {observed[0].selection_id}
    if scanned:
        assert report.text_prefilter_scans == 1 and report.analyzed == 1
    else:
        assert world.bill("4100").status is BillStatus.DOCUMENT_UNSUPPORTED
        assert not world.llm.contexts


def test_outage_creates_no_completed_source_decision() -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o podatku")
    world.gateway.outages.add("download")

    report = world.run(track=False)

    assert report.errors
    assert world.bill("4100").status is BillStatus.TEXT_PREFILTER_PENDING
    assert not world.repo.list_source_observations(10, "4100")


def test_status_failure_rolls_back_the_source_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(extractor=FakeTextExtractor("Art. 1. Podatek wynosi 10 zł. " * 30))
    world.add_bill("4100", "Projekt ustawy o podatku")
    world.repo.upsert_summary(world.gateway.processes[0], now=world.clock.now())
    before = world.repo.dump()

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("status write failed")

    monkeypatch.setattr(world.repo, "set_status", fail)
    service = world.container.text_prefilter_service()
    assert service is not None, "World enables text prefilter by default"

    with pytest.raises(RuntimeError, match="status write failed"):
        service.check(world.bill("4100"))

    assert world.repo.dump() == before
    assert not world.repo.list_source_observations(10, "4100")

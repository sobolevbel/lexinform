import hashlib
import threading
from dataclasses import replace

import pytest

from lexinform.document_fingerprints import text_digest
from lexinform.errors import ServiceUnavailableError
from lexinform.law_digest import law_digest
from lexinform.models import (
    AnalysisRecord,
    DocumentSelectionRecord,
    SourceObservation,
    TextDocument,
)
from lexinform.models.document_reads import LoadedFile
from tests.fakes import FakeSejmGateway, FakeTextExtractor, make_analysis
from tests.harness import TERM, World
from tests.scenario.test_law_comparison_safety import ACT

OLD_URL = "https://api.test/historical.pdf"
NEW_URL = "https://api.test/current.pdf"
OLD_BODY = b"%PDF-historical"
NEW_BODY = b"%PDF-current"
CHANGED = ACT.replace("1000", "1001")
NEW_REASONS = ACT + "\nUZASADNIENIE\n" + "Nowe uzasadnienie projektu. " * 30


def historical_world(*, legacy: bool, workers: int = 1) -> tuple[World, AnalysisRecord]:
    world = World(
        workers=workers,
        extractor=FakeTextExtractor(
            by_content={OLD_BODY: ACT, NEW_BODY: NEW_REASONS if legacy else CHANGED},
            page_count=1,
        ),
    )
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.repo.upsert_summary(world.gateway.processes[0], now=world.clock.now())
    previous = AnalysisRecord(
        analysis=make_analysis(),
        model="historical-model",
        prompt_version="historical-prompt",
        input_chars=len(ACT),
        truncated=False,
        text_source="pdf",
        created_at=world.clock.now(),
        source_url=OLD_URL,
        text_sha256=text_digest(ACT),
        law_sha256=None if legacy else law_digest(ACT),
    )
    world.repo.save_analysis(TERM, "4100", previous)
    world.gateway.files.update({OLD_URL: OLD_BODY, NEW_URL: NEW_BODY})
    world.clock.advance(days=10)
    return world, previous


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("workers", [1, 4])
def test_current_historical_reread_has_real_owner_without_recovering_paid_provenance(
    legacy: bool, workers: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, previous = historical_world(legacy=legacy, workers=workers)
    caller = threading.get_ident()
    writes: list[int] = []
    save = world.repo.save_source_observation

    def record(selection: DocumentSelectionRecord, observation: SourceObservation) -> None:
        writes.append(threading.get_ident())
        save(selection, observation)

    monkeypatch.setattr(world.repo, "save_source_observation", record)
    bill = world.bill("4100")

    fresh, changed = world.analysis.prepare_reanalysis(
        bill, TextDocument(url=NEW_URL, kind="print")
    )

    assert changed is not legacy
    observations = world.repo.list_source_observations(TERM, "4100")
    assert {item.document.url for item in observations} == {OLD_URL, NEW_URL}
    old = next(item for item in observations if item.document.url == OLD_URL)
    assert old.purpose == "analysis" and old.recorded_at == world.clock.now()
    assert old.recorded_at > previous.created_at
    selected = world.repo.get_document_selection(old.selection_id)
    assert selected is not None, "The reread retains its current physical selection"
    assert selected.selection.policy_version is not None
    assert selected.selection.scan_input is None and not any(
        part.text for part in selected.selection.parts
    )
    links = world.repo.list_selection_artifacts(old.selection_id)
    assert links and links[0].artifact_sha256 == hashlib.sha256(OLD_BODY).hexdigest()
    assert world.repo.list_artifact_reads(requested_url=OLD_URL)
    assert writes and set(writes) == {caller}
    assert world.bill("4100").analysis == previous
    if legacy:
        assert fresh.analysis is not None, "Quiet comparison retains the previous answer"
        assert fresh.analysis.analysis == previous.analysis
        assert fresh.analysis.created_at == previous.created_at
        assert fresh.analysis.computation_id is None and fresh.analysis.request_sha256 is None
        assert fresh.analysis.selection.scan_input is None
        assert not world.llm.contexts and not world.llm.change_contexts
        assert not world.repo.list_computation_results(TERM, "4100")
        world.clock.advance(days=1)
        world.analysis.prepare_reanalysis(bill, TextDocument(url=NEW_URL, kind="print"))
        assert world.repo.get_source_observation(old.observation_id) == old


@pytest.mark.parametrize("legacy", [False, True])
def test_completed_blocked_historical_read_is_observed_before_fallback(
    legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, previous = historical_world(legacy=legacy)
    loader = world.container.text_loader()
    select = loader.select_document

    def unsupported(document: TextDocument) -> tuple[TextDocument, LoadedFile]:
        selected, loaded = select(document)
        if document.url == OLD_URL:
            loaded = replace(
                loaded,
                selection=loaded.selection.model_copy(
                    update={"state": "unsupported", "rejected": ("blocked historical selection",)}
                ),
            )
        return selected, loaded

    monkeypatch.setattr(loader, "select_document", unsupported)

    world.analysis.prepare_reanalysis(world.bill("4100"), TextDocument(url=NEW_URL, kind="print"))

    old = next(
        item
        for item in world.repo.list_source_observations(TERM, "4100")
        if item.document.url == OLD_URL
    )
    selected = world.repo.get_document_selection(old.selection_id)
    assert selected is not None, "A completed blocked selection has current source metadata"
    assert selected.selection.state == "unsupported"
    assert world.repo.list_artifact_reads(requested_url=OLD_URL)
    assert world.bill("4100").analysis == previous
    assert len(world.llm.contexts) == 1 and not world.llm.change_contexts


@pytest.mark.parametrize("legacy", [False, True])
def test_historical_metadata_failure_is_not_swallowed_as_read_fallback(
    legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, previous = historical_world(legacy=legacy)
    failure = OSError("historical metadata persistence failed")
    save = world.repo.save_source_observation

    def record(selection: DocumentSelectionRecord, observation: SourceObservation) -> None:
        save(selection, observation)
        if observation.document.url == OLD_URL:
            raise failure

    monkeypatch.setattr(world.repo, "save_source_observation", record)

    with pytest.raises(OSError) as caught:
        world.analysis.prepare_reanalysis(
            world.bill("4100"), TextDocument(url=NEW_URL, kind="print")
        )

    assert caught.value is failure
    assert not world.llm.contexts and not world.llm.change_contexts
    assert world.bill("4100").analysis == previous
    assert not world.repo.get_artifact_metadata(hashlib.sha256(OLD_BODY).hexdigest())
    assert not world.repo.list_artifact_reads(requested_url=OLD_URL)
    assert all(
        item.document.url != OLD_URL for item in world.repo.list_source_observations(TERM, "4100")
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_historical_outage_preserves_each_callers_existing_policy(
    legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, previous = historical_world(legacy=legacy)
    options = world.container.analysis_options()
    if legacy:
        options = replace(options, change_review_max_ratio=0)
    container = replace(world.container, analysis_options_override=options)
    analysis = container.analysis_service()
    loader = container.text_loader()
    read = loader.read
    failure = ServiceUnavailableError("historical host unavailable")

    def unavailable(url: str) -> LoadedFile:
        if url == OLD_URL:
            raise failure
        return read(url)

    monkeypatch.setattr(loader, "read", unavailable)
    if legacy:
        fresh, changed = analysis.prepare_reanalysis(
            world.bill("4100"), TextDocument(url=NEW_URL, kind="print")
        )
        assert changed and fresh.analysis is not None
    else:
        with pytest.raises(ServiceUnavailableError) as caught:
            analysis.prepare_reanalysis(world.bill("4100"), TextDocument(url=NEW_URL, kind="print"))
        assert caught.value is failure
    assert world.bill("4100").analysis == previous
    assert not world.repo.list_artifact_reads(requested_url=OLD_URL)
    assert all(
        item.document.url != OLD_URL for item in world.repo.list_source_observations(TERM, "4100")
    )


def test_package_and_original_historical_reads_keep_distinct_current_relations() -> None:
    world, _ = historical_world(legacy=False)
    extra = "https://api.test/historical-reasons.pdf"
    world.gateway.files[extra] = b"historical supporting body"
    assert isinstance(world.extractor, FakeTextExtractor), (
        "The fixture uses content-mapped extraction"
    )
    world.extractor.by_content[b"historical supporting body"] = (
        "UZASADNIENIE\n" + "Opis uzasadnienia projektu. " * 30
    )
    previous_document = TextDocument(url=OLD_URL, kind="print", extra_urls=(extra,))

    world.analysis.prepare_reanalysis(
        world.bill("4100"),
        TextDocument(url=NEW_URL, kind="print"),
        previous_document=previous_document,
    )

    observations = [
        item
        for item in world.repo.list_source_observations(TERM, "4100")
        if item.document.url == OLD_URL
    ]
    assert {item.document.extra_urls for item in observations} == {(), (extra,)}
    assert len(world.llm.change_contexts) == 1


@pytest.mark.parametrize("late", [False, True])
def test_historical_scan_outage_keeps_wrapped_and_direct_cause_chains(
    late: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = OSError("historical transport failed")
    failure = ServiceUnavailableError("historical scan unavailable")
    download = FakeSejmGateway.download
    calls = 0

    def interrupted(gateway: FakeSejmGateway, url: str, *, max_bytes: int | None = None) -> bytes:
        nonlocal calls
        if url == OLD_URL:
            calls += 1
            if calls == (2 if late else 1):
                raise failure from transport
        return download(gateway, url, max_bytes=max_bytes)

    monkeypatch.setattr(FakeSejmGateway, "download", interrupted)
    world, previous = historical_world(legacy=False)
    assert isinstance(world.extractor, FakeTextExtractor), "The fixture extracts a one-page scan"
    world.extractor.by_content[OLD_BODY] = ""
    causes: list[BaseException | None] = []
    save = world.repo.save_source_observation

    def record(selection: DocumentSelectionRecord, observation: SourceObservation) -> None:
        if observation.document.url == NEW_URL:
            causes.append(failure.__cause__)
        save(selection, observation)

    monkeypatch.setattr(world.repo, "save_source_observation", record)

    with pytest.raises(ServiceUnavailableError) as caught:
        world.analysis.prepare_reanalysis(
            world.bill("4100"), TextDocument(url=NEW_URL, kind="print")
        )

    assert caught.value is failure and len(causes) == 1
    if late:
        assert type(causes[0]).__name__ == "_SourceReadError"
        assert causes[0] is not None, "The admitted source wraps its later scan-read outage"
        assert causes[0].__cause__ is failure
    else:
        assert causes[0] is transport
    assert bool(world.repo.list_artifact_reads(requested_url=OLD_URL)) is late
    assert world.bill("4100").analysis == previous
    assert not world.llm.contexts and not world.llm.change_contexts


def test_outer_rollback_removes_current_historical_observation_graph() -> None:
    world, _ = historical_world(legacy=True)
    before = world.repo.dump()
    world.repo.begin()

    world.analysis.prepare_reanalysis(world.bill("4100"), TextDocument(url=NEW_URL, kind="print"))
    assert world.repo.list_source_observations(TERM, "4100")
    world.repo.rollback()

    assert world.repo.dump() == before

import hashlib
import threading

import pytest

from lexinform.errors import ServiceUnavailableError
from lexinform.models import DocumentSelectionRecord, SourceObservation, TextDocument
from tests.fakes import FakeTextExtractor
from tests.harness import World
from tests.scenario.test_senate_document_selection import (
    FIXTURES,
    STAGE,
    SUBJECT,
    world_with_sources,
)


def senate_observations(world: World) -> list[SourceObservation]:
    return [
        o
        for o in world.repo.list_source_observations(10, "1929")
        if o.document.kind == "senate_amendments"
    ]


def selected_document(world: World) -> TextDocument:
    document = world.analysis.senate_amendments_document(
        world.gateway.prints["3213"], STAGE, titles=(SUBJECT,)
    )
    assert document is not None, "the fixture contains a confirmed resolution or eligible scan"
    return document


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("name", ["3213.docx", "3213.pdf"])
def test_senate_source_metadata_is_separate_from_admission_and_written_on_the_caller(
    workers: int, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = (FIXTURES / "senate_3213_resolution.txt").read_text()
    world = world_with_sources({name: text}, workers=workers)
    caller = threading.get_ident()
    threads: list[int] = []
    save = world.repo.save_source_observation

    def record(selection: DocumentSelectionRecord, observation: SourceObservation) -> None:
        if observation.document.kind == "senate_amendments":
            threads.append(threading.get_ident())
        save(selection, observation)

    monkeypatch.setattr(world.repo, "save_source_observation", record)

    world.run()
    world.repo.restore(world.repo.dump())

    observed = senate_observations(world)
    assert len(observed) == 1 and threads == [caller]
    choice = world.repo.get_document_selection(observed[0].selection_id)
    assert choice is not None, "the source observation retains its physical metadata"
    assert choice.selection.question == "senate_resolution_source"
    assert choice.selection.policy_version == "senate-source-observation-v1"
    assert choice.selection.state == "unverified" and choice.selection.scan_input is None
    assert choice.selection.parts[0].role == "unknown" and not choice.selection.parts[0].text
    assert choice.selection.parts[0].text_sha256 == hashlib.sha256(text.encode()).hexdigest()
    links = world.repo.list_selection_artifacts(observed[0].selection_id)
    assert links and links[0].artifact_sha256 == hashlib.sha256(name.encode()).hexdigest()
    assert world.llm.amendment_contexts[0].text == text


def test_senate_scan_keeps_actual_source_separate_from_the_provider_input() -> None:
    name = "3213.pdf"
    world = world_with_sources({name: (FIXTURES / "senate_3213_cover.txt").read_text()})

    world.run()

    observed = senate_observations(world)
    assert len(observed) == 1
    choice = world.repo.get_document_selection(observed[0].selection_id)
    assert choice is not None, "a successful full scan has a distinct source metadata view"
    assert choice.selection.scan_input is None and not choice.selection.parts[0].text_sha256
    assert choice.selection.parts[0].sha256 == hashlib.sha256(name.encode()).hexdigest()
    assert world.repo.list_selection_artifacts(observed[0].selection_id)
    assert world.batch.submitted and not world.llm.amendment_contexts


@pytest.mark.parametrize("scanned", [False, True])
def test_senate_changed_body_is_observed_without_substituting_the_expected_hash(
    scanned: bool,
) -> None:
    name = "3213.pdf" if scanned else "3213.docx"
    fixture = "senate_3213_cover.txt" if scanned else "senate_3213_resolution.txt"
    text = (FIXTURES / fixture).read_text()
    world = world_with_sources({name: text})
    document = selected_document(world)
    assert document.senate is not None, "the selector supplies the expected physical identity"
    if not scanned:
        for index in range(40):
            url = f"https://api.test/eviction-{index}"
            world.gateway.files[url] = b"other"
            world.container.text_loader().read(url)
    world.gateway.files[document.url] = b"changed"
    assert isinstance(world.extractor, FakeTextExtractor), "the fixture exposes byte-dependent text"
    world.extractor.by_content[b"changed"] = text.replace("31 grudnia", "30 grudnia")

    with pytest.raises(ValueError, match="изменился"):
        world.analysis.summarize_amendments(world.bill("1929"), document)

    observed = senate_observations(world)
    assert len(observed) == 1 and observed[0].document.senate == document.senate
    choice = world.repo.get_document_selection(observed[0].selection_id)
    assert choice is not None, "the refused body retains its actual physical identity"
    assert choice.selection.parts[0].sha256 == hashlib.sha256(b"changed").hexdigest()
    assert choice.selection.parts[0].sha256 != document.senate.sha256
    assert not world.batch.submitted and not world.llm.amendment_contexts


def test_repeated_senate_preparation_preserves_first_recording_and_the_exact_dump() -> None:
    world = world_with_sources({"3213.docx": (FIXTURES / "senate_3213_resolution.txt").read_text()})
    document = selected_document(world)
    assert world.analysis.amendments_context(world.bill("1929"), document) is not None
    first = senate_observations(world)[0]
    before = world.repo.dump()
    world.clock.advance(days=1)

    assert world.analysis.amendments_context(world.bill("1929"), document) is not None

    assert world.repo.dump() == before
    assert senate_observations(world) == [first]


def test_senate_scan_source_outage_creates_no_completed_observation() -> None:
    world = world_with_sources({"3213.pdf": (FIXTURES / "senate_3213_cover.txt").read_text()})
    document = selected_document(world)
    world.gateway.outages.add("download")

    with pytest.raises(ServiceUnavailableError):
        world.analysis.summarize_amendments(world.bill("1929"), document)

    assert not senate_observations(world)

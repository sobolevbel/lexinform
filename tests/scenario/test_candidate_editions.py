import threading

import pytest

from lexinform.models import DocumentEdition, TextDocument
from tests.fakes import FakeTextExtractor
from tests.harness import World, print_url
from tests.scenario.test_document_selection_contract import law


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("batch", [False, True])
def test_source_edition_precedes_input_fitting_and_does_not_change_memo_reuse(
    workers: int, batch: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = law().replace("Art. 2.", "Art. 2. " + "Cudzoziemiec składa wniosek osobiście. " * 300)
    world = World(
        workers=workers,
        batch_only=batch,
        extractor=FakeTextExtractor(body),
        text_budget_chars=1000,
    )
    for number in ("4100", "4101"):
        world.add_bill(number, "Projekt ustawy o cudzoziemcach")
    save = world.repo.save_selection_edition
    threads: list[int] = []

    def record(selection_id: str, edition: DocumentEdition) -> None:
        threads.append(threading.get_ident())
        save(selection_id, edition)

    monkeypatch.setattr(world.repo, "save_selection_edition", record)

    world.run(track=False)
    if batch:
        world.batch.resolve()
        world.run(track=False)

    assert threads == [threading.get_ident()] * 2
    first = world.repo.list_source_observations(10, "4100")[0]
    parent = world.repo.get_document_selection(first.selection_id)
    link = world.repo.get_selection_edition(first.selection_id)
    assert parent is not None and link is not None, "the caller records known source membership"
    edition = world.repo.get_document_edition(link.edition_id)
    assert edition is not None, "the immutable reference resolves its exact candidate set"
    assert not any(span.stage == "analysis" for span in parent.selection.coverage)
    paid = world.bill("4100").analysis
    assert paid is not None and paid.truncated, "the input budget fits the later paid input"
    before = len(world.llm.contexts)
    alias = TextDocument(url="https://api.test/reprinted.pdf", kind="print")
    world.gateway.files[alias.url] = world.gateway.files[print_url("4100")]
    world.clock.advance(days=1)

    assert world.analysis.reanalyze_bill(world.bill("4100"), alias) is None

    later = next(o for o in world.repo.list_source_observations(10, "4100") if o.document == alias)
    assert world.repo.get_selection_edition(later.selection_id) is not None
    assert world.repo.get_selection_edition(later.selection_id) == link.model_copy(
        update={"selection_id": later.selection_id}
    )
    assert world.repo.get_document_edition(link.edition_id) == edition
    assert len(world.llm.contexts) == before
    current = world.bill("4100").analysis
    assert current is not None and current.computation_id == paid.computation_id
    world.repo.restore(world.repo.dump())
    assert world.repo.get_selection_edition(first.selection_id) == link


@pytest.mark.parametrize("scanned", [False, True])
def test_missing_candidate_core_keeps_source_history_without_an_edition(scanned: bool) -> None:
    world = World(extractor=FakeTextExtractor("", page_count=12 if scanned else 0))
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")

    world.run(track=False)

    observations = world.repo.list_source_observations(10, "4100")
    assert observations
    assert all(world.repo.get_selection_edition(row.selection_id) is None for row in observations)


def test_source_status_failure_rolls_back_new_candidate_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(extractor=FakeTextExtractor(law()))
    world.add_bill("4100", "Projekt ustawy o podatku")
    world.repo.upsert_summary(world.gateway.processes[0], now=world.clock.now())
    before = world.repo.dump()
    writes: list[str] = []
    save = world.repo.save_selection_edition

    def record(selection_id: str, edition: DocumentEdition) -> None:
        writes.append(edition.edition_id)
        save(selection_id, edition)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("status write failed")

    monkeypatch.setattr(world.repo, "save_selection_edition", record)
    monkeypatch.setattr(world.repo, "set_status", fail)
    prefilter = world.container.text_prefilter_service()
    assert prefilter is not None, "World enables text prefilter by default"

    with pytest.raises(RuntimeError, match="status write failed"):
        prefilter.check(world.bill("4100"))

    assert writes and world.repo.dump() == before

import hashlib
import threading

import pytest

from lexinform.document_fingerprints import text_digest
from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    DocumentSelectionRecord,
    ReviewDocument,
    ReviewSource,
    SourceObservation,
    TextDocument,
)
from tests.harness import World, print_url


def review_observations(world: World) -> list[SourceObservation]:
    return [o for o in world.repo.list_source_observations(10, "4100") if o.purpose == "review"]


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("compare_versions", [False, True])
def test_review_records_source_without_changing_context_or_inventing_a_paid_result(
    workers: int, compare_versions: bool
) -> None:
    world = World(
        workers=workers,
        consultation_reviews_enabled=True,
        batch_kinds=frozenset({"analysis", "reanalysis", "consultation_review"}),
    )
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    before = len(world.llm.contexts)
    source = ReviewSource(
        identity="review/1",
        role="response",
        document=TextDocument(
            url=print_url("4100"),
            kind="print",
            compare_versions=compare_versions,
            alternatives=(
                TextDocument(
                    url="https://api.test/conflicting-review-link.pdf",
                    kind="print",
                    official_link_conflict=True,
                ),
            )
            if compare_versions
            else (),
        ),
    )

    context = world.analysis.review_context(world.bill("4100"), "consultation_review", (source,))
    world.repo.restore(world.repo.dump())

    assert context is not None and len(context.documents) == 1
    observations = review_observations(world)
    assert len(observations) == 1
    selected = world.repo.get_document_selection(observations[0].selection_id)
    assert selected is not None, "review source metadata is retained independently of a paid result"
    assert not any(p.text for p in selected.selection.parts)
    assert world.repo.list_selection_artifacts(observations[0].selection_id)
    traces = world.repo.list_candidate_disposition_traces(observations[0].observation_id)
    assert len(traces) == (1 if compare_versions else 0)
    if compare_versions:
        assert traces[0].requested_document == source.document
        assert [item.outcome for item in traces[0].dispositions] == [
            "non_bill",
            "official_conflict",
            "selected",
            "unattempted",
        ]
    assert len(world.llm.contexts) == before
    assert not world.repo.list_computation_results(10, "4100", purpose="consultation_review")
    first = observations[0]
    world.clock.advance(days=1)
    assert (
        world.analysis.review_context(world.bill("4100"), "consultation_review", (source,))
        == context
    )
    assert review_observations(world) == [first]


def test_refused_historical_digest_keeps_source_metadata_without_claiming_admission() -> None:
    world = World(
        consultation_reviews_enabled=True,
        batch_kinds=frozenset({"analysis", "reanalysis", "consultation_review"}),
    )
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    source = ReviewSource(
        identity="review/1",
        role="project_text",
        expected_digest="f" * 64,
        document=TextDocument(url=print_url("4100"), kind="print"),
    )

    context = world.analysis.review_context(world.bill("4100"), "consultation_review", (source,))

    assert context is None
    assert len(review_observations(world)) == 1


def test_saved_review_document_reuse_does_not_fabricate_a_new_source_read() -> None:
    world = World(
        consultation_reviews_enabled=True,
        batch_kinds=frozenset({"analysis", "reanalysis", "consultation_review"}),
    )
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    text = "Pkt 1. Uwaga uwzględniona. " * 30
    saved = ReviewDocument(
        identity="saved",
        url="https://api.test/unfetched.pdf",
        role="response",
        text=text,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        normalized_sha256=text_digest(text),
    )
    source = ReviewSource(
        identity=saved.identity,
        role=saved.role,
        document=TextDocument(url=saved.url, kind="print"),
        snapshot=saved,
    )
    calls = tuple(world.gateway.calls)

    context = world.analysis.review_context(world.bill("4100"), "consultation_review", (source,))

    assert context is not None and context.documents == (saved,)
    assert tuple(world.gateway.calls) == calls
    assert not review_observations(world)


@pytest.mark.parametrize("workers", [1, 4])
def test_review_metadata_writes_are_on_the_calling_thread(
    workers: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(
        workers=workers,
        consultation_reviews_enabled=True,
        batch_kinds=frozenset({"analysis", "reanalysis", "consultation_review"}),
    )
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    sources = tuple(
        ReviewSource(
            identity=str(index),
            role="response",
            document=TextDocument(url=f"https://api.test/review-{index}.pdf", kind="print"),
        )
        for index in range(3)
    )
    for source in sources:
        world.gateway.files[source.document.url] = source.identity.encode()
    caller = threading.get_ident()
    threads: list[int] = []
    save = world.repo.save_source_observation

    def record(selection: DocumentSelectionRecord, observation: SourceObservation) -> None:
        threads.append(threading.get_ident())
        save(selection, observation)

    monkeypatch.setattr(world.repo, "save_source_observation", record)

    assert (
        world.analysis.review_context(world.bill("4100"), "consultation_review", sources)
        is not None
    )

    assert threads == [caller] * 3


def test_review_source_outage_preserves_the_existing_error_policy_and_no_completed_read() -> None:
    world = World(
        consultation_reviews_enabled=True,
        batch_kinds=frozenset({"analysis", "reanalysis", "consultation_review"}),
    )
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    world.gateway.outages.add("download")
    source = ReviewSource(
        identity="new",
        role="response",
        document=TextDocument(url="https://api.test/unavailable.pdf", kind="print"),
    )

    with pytest.raises(ServiceUnavailableError):
        world.analysis.review_context(world.bill("4100"), "consultation_review", (source,))

    assert not review_observations(world)


def test_review_metadata_failure_rolls_back_the_entire_source_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(
        consultation_reviews_enabled=True,
        batch_kinds=frozenset({"analysis", "reanalysis", "consultation_review"}),
    )
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    source = ReviewSource(
        identity="new",
        role="response",
        document=TextDocument(url="https://api.test/new.pdf", kind="print"),
    )
    world.gateway.files[source.document.url] = b"new review body"
    before = world.repo.dump()
    save = world.repo.save_source_observation

    def fail(selection: DocumentSelectionRecord, observation: SourceObservation) -> None:
        save(selection, observation)
        raise RuntimeError("metadata failure")

    monkeypatch.setattr(world.repo, "save_source_observation", fail)

    with pytest.raises(RuntimeError, match="metadata failure"):
        world.analysis.review_context(world.bill("4100"), "consultation_review", (source,))

    assert world.repo.dump() == before

import hashlib
import threading
from typing import Literal

import pytest

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    DocumentSelectionRecord,
    SourceObservation,
    SupplementRecord,
    TextDocument,
)
from lexinform.services.analysis import Waiting
from tests.fakes import FakeTextExtractor
from tests.harness import World
from tests.scenario.test_secondary_computation_history import URL, ask, prepared_world


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("kind", ["amendments", "supplement"])
def test_secondary_source_is_recorded_before_batch_submission_on_the_calling_thread(
    kind: Literal["amendments", "supplement"], workers: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = prepared_world(workers=workers)
    submitted = len(world.batch.submitted)
    caller = threading.get_ident()
    threads: list[int] = []
    save = world.repo.save_source_observation

    def record(selection: DocumentSelectionRecord, observation: SourceObservation) -> None:
        threads.append(threading.get_ident())
        save(selection, observation)

    monkeypatch.setattr(world.repo, "save_source_observation", record)

    assert isinstance(ask(world, kind), Waiting)
    world.repo.restore(world.repo.dump())

    observations = [
        o for o in world.repo.list_source_observations(10, "3039") if o.document.url == URL
    ]
    assert len(observations) == 1 and observations[0].purpose == "analysis"
    first = observations[0]
    assert threads == [caller]
    assert len(world.batch.submitted) == submitted
    links = world.repo.list_selection_artifacts(first.selection_id)
    assert links and links[0].artifact_sha256 == hashlib.sha256(b"%PDF-secondary").hexdigest()
    world.clock.advance(days=1)
    assert isinstance(ask(world, kind), Waiting)
    assert world.repo.get_source_observation(first.observation_id) == first


def test_recommendation_source_has_its_own_observation() -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    main = TextDocument(url="https://api.test/amendments.pdf", kind="committee_amendments")
    report = TextDocument(url="https://api.test/recommendation.pdf", kind="committee_amendments")
    world.gateway.files[main.url] = b"amendments"
    world.gateway.files[report.url] = b"recommendation"

    context = world.analysis.amendments_context(world.bill("4100"), main, recommendation=report)

    assert context is not None
    observed = world.repo.list_source_observations(10, "4100")
    assert {o.document.url for o in observed} >= {main.url, report.url}
    assert all(world.repo.list_selection_artifacts(o.selection_id) for o in observed)


def test_full_committee_input_refusal_preserves_the_completed_source() -> None:
    world = World(
        text_budget_chars=1000,
        extractor=FakeTextExtractor(
            by_content={b"long": "Art. 1. Cudzoziemiec składa wniosek. " * 400}
        ),
    )
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    document = TextDocument(url="https://api.test/long.pdf", kind="committee_amendments")
    world.gateway.files[document.url] = b"long"

    with pytest.raises(ValueError, match="поправок комиссии"):
        world.analysis.amendments_context(world.bill("4100"), document)

    observed = [
        o for o in world.repo.list_source_observations(10, "4100") if o.document.url == document.url
    ]
    assert len(observed) == 1
    assert world.repo.list_selection_artifacts(observed[0].selection_id)


@pytest.mark.parametrize("outage", [False, True])
def test_secondary_source_failure_keeps_admission_and_outage_distinct(outage: bool) -> None:
    extractor = FakeTextExtractor()
    world = World(extractor=extractor)
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    original = world.bill("4100").analysis
    document = TextDocument(url="https://api.test/unreadable.pdf", kind="government_position")
    world.gateway.files[document.url] = b"unreadable"
    extractor.text = ""
    extractor.page_count = 0
    if outage:
        world.gateway.outages.add("download")

    if outage:
        with pytest.raises(ServiceUnavailableError):
            world.analysis.digest_supplement(
                world.bill("4100"), document, number="4101", title="Stanowisko"
            )
    else:
        result = world.analysis.digest_supplement(
            world.bill("4100"), document, number="4101", title="Stanowisko"
        )
        assert isinstance(result, SupplementRecord) and result.digest is None
        assert not world.llm.supplement_contexts

    observed = [
        o for o in world.repo.list_source_observations(10, "4100") if o.document.url == document.url
    ]
    assert len(observed) == (0 if outage else 1)
    assert world.bill("4100").analysis == original

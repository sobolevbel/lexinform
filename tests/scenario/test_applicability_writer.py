import datetime as dt

import pytest

from lexinform.models import AnalysisRecord, ResultApplicabilityObservation, TextDocument
from lexinform.services.analysis_applications import apply_analysis
from tests.harness import World
from tests.scenario.test_result_applications import collected_world


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
def test_equivalent_source_versions_keep_paid_answer_and_separate_associations(
    workers: int, model: str
) -> None:
    world = World(profile="production", workers=workers, analysis_model=model)
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    paid = world.repo.list_computation_results(10, "3039")[0]
    receipts = world.repo.list_result_applications(10, "3039")
    original = world.bill("3039").analysis
    assert original is not None, "the completed primary analysis supplies the initial source"
    sources = [original.source_url]
    assert len(world.repo.list_applicability_observations(10, "3039")) == 1
    for version in ("b", "c"):
        world.clock.advance(hours=1)
        url = f"https://api.test/version-{version}.pdf"
        world.gateway.files[url] = b"%PDF"
        assert (
            world.analysis.reanalyze_bill(world.bill("3039"), TextDocument(url=url, kind="print"))
            is None
        )
        sources.append(url)
        world.repo.restore(world.repo.dump())
    observations = world.repo.list_applicability_observations(10, "3039")
    assert [row.source.url for row in observations] == sources
    assert all(row.result_id == paid.result_id for row in observations)
    assert world.repo.get_computation_result(paid.result_id) == paid
    assert world.repo.list_result_applications(10, "3039") == receipts
    assert len(world.batch.submitted) == 1 and not world.llm.contexts
    current = world.bill("3039").analysis
    assert current is not None, "quiet reuse retains the applied analysis"
    before = world.repo.dump()
    apply_analysis(world.repo, 10, "3039", current, now=world.clock.now() + dt.timedelta(days=1))
    assert world.repo.dump() == before


@pytest.mark.parametrize("workers", [1, 4])
def test_collection_waits_for_projection_before_recording_applicability(workers: int) -> None:
    world = collected_world(workers=workers)
    assert not world.repo.list_applicability_observations(10, "3039")
    world.analysis.analyze_bill(world.bill("3039"))
    observations = world.repo.list_applicability_observations(10, "3039")
    assert len(observations) == 1
    assert observations[0].result_id == world.repo.list_computation_results(10, "3039")[0].result_id


def test_legacy_and_unknown_sources_do_not_gain_invented_associations() -> None:
    world = World()
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    assert not world.repo.list_applicability_observations(10, "3039")
    assert not world.repo.list_computation_results(10, "3039")


@pytest.mark.parametrize("workers", [1, 4])
def test_association_failure_rolls_back_projection_and_keeps_paid_result_retryable(
    workers: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = collected_world(workers=workers)
    before = world.repo.dump()
    save = world.repo.save_applicability_observation

    def crash(observation: ResultApplicabilityObservation) -> None:
        save(observation)
        raise RuntimeError("injected applicability checkpoint failure")

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, "save_applicability_observation", crash)
        with pytest.raises(RuntimeError, match="applicability checkpoint"):
            world.analysis.analyze_bill(world.bill("3039"))
    assert world.repo.dump() == before
    world.analysis.analyze_bill(world.bill("3039"))
    assert len(world.repo.list_applicability_observations(10, "3039")) == 1
    assert len(world.batch.submitted) == len(world.repo.list_computation_results(10, "3039")) == 1


def test_outer_tracking_rollback_also_removes_the_source_association() -> None:
    world = collected_world(workers=1)
    before = world.repo.dump()
    with pytest.raises(RuntimeError, match="outer checkpoint"), world.repo.atomic():
        world.analysis.analyze_bill(world.bill("3039"))
        assert len(world.repo.list_applicability_observations(10, "3039")) == 1
        raise RuntimeError("outer checkpoint failed")
    assert world.repo.dump() == before


def test_missing_projection_owner_does_not_record_an_orphan_association() -> None:
    world = collected_world(workers=1)
    paid = world.repo.list_computation_results(10, "3039")[0]
    assert isinstance(paid.record, AnalysisRecord), "the primary result contains an analysis record"
    apply_analysis(world.repo, 10, "absent", paid.record, now=world.clock.now())
    assert not world.repo.list_applicability_observations(10, "absent")

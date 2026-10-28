from collections.abc import Iterator
from typing import Literal

import pytest

from lexinform.models import AnalysisRecord, BatchRequest, BatchResult, BillStatus, TokenUsage
from lexinform.models.report import CallKind
from tests.fakes import FakeLlm
from tests.harness import World


@pytest.mark.parametrize("workers", [1, 4])
def test_paid_primary_answer_survives_supersession_and_operational_cleanup(workers: int) -> None:
    world = World(profile="production", workers=workers)
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    request = world.batch.submitted[0]
    world.command("/skip 3039")
    world.run(track=False)
    world.batch.resolve()

    world.run(track=False)
    world.clock.advance(days=200)
    world.run(track=False)
    world.repo.restore(world.repo.dump())

    results = world.repo.list_computation_results(10, "3039")
    assert len(results) == 1
    result = results[0]
    assert isinstance(result.record, AnalysisRecord), (
        "the primary answer retains its analysis record"
    )
    assert result.record.request_sha256 == request.payload_sha256
    assert result.record.computation_id == result.result_id
    assert result.record.analysis.relevant
    applications = world.repo.list_result_applications(10, "3039")
    assert len(applications) == 1 and applications[0].outcome == "superseded"
    assert applications[0].reason == "generation_changed"
    assert world.bill("3039").status is BillStatus.SKIPPED_PREFILTER
    assert world.bill("3039").analysis is None
    assert not world.repo.load_analysis_memo()
    assert len(world.batch.submitted) == 1 and not world.publisher.new_bills


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("fault", ["mark_llm_batch_item_consumed", "save_result_application"])
def test_collect_history_and_ready_checkpoint_roll_back_together(
    workers: int, fault: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(profile="production", workers=workers)
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    batch = world.repo.list_open_llm_batches()[0]
    world.batch.resolve()

    def crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected failure before consumed checkpoint")

    with monkeypatch.context() as patch:
        patch.setattr(world.repo, fault, crash)
        with pytest.raises(RuntimeError, match="injected failure"):
            world.analysis.collect_batches()
    assert not world.repo.list_computation_results(10, "3039")
    assert not world.repo.list_result_applications(10, "3039")
    assert world.bill("3039").ready_analysis is None
    assert world.bill("3039").status is BillStatus.BATCH_PENDING
    assert len(world.repo.list_llm_batch_items(batch.batch_id)) == 1
    assert not world.repo.load_analysis_memo()

    world.run(track=False)
    record = world.bill("3039").analysis
    assert record is not None, "the next collection retries the unconsumed provider item"
    results = world.repo.list_computation_results(10, "3039")
    assert len(results) == 1 and results[0].result_id == record.computation_id
    assert (
        len(
            [
                row
                for row in world.repo.list_result_applications(10, "3039")
                if row.phase == "collection"
            ]
        )
        == 1
    )
    world.run(track=False)
    assert len(world.repo.list_computation_results(10, "3039")) == 1
    assert len(world.batch.submitted) == 1 and len(world.publisher.new_bills) == 1


@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
@pytest.mark.parametrize("legacy", [False, True])
def test_collected_result_is_independent_of_projection_memo_and_request_retention(
    model: str, legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(profile="production", analysis_model=model)
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    prepare = world.batch.prepare_request

    def old_request(request: BatchRequest) -> BatchRequest:
        return prepare(request).model_copy(update={"payload_sha256": None})

    with monkeypatch.context() as patch:
        if legacy:
            patch.setattr(world.batch, "prepare_request", old_request)
        world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    result = world.repo.list_computation_results(10, "3039")[0]
    record = result.record
    assert isinstance(record, AnalysisRecord), "the retained primary computation has its analysis"
    assert record.input_tokens == FakeLlm.ANALYSIS_TOKENS[0]
    assert result.usage.batch_input == record.input_tokens
    assert result.provider == ("openai" if model.startswith("gpt-") else "anthropic")
    assert (result.request_sha256 is None) == legacy
    world.repo.save_analysis(
        10, "3039", record.model_copy(update={"source_url": "https://new.test"})
    )
    world.clock.advance(days=200)
    world.run(track=False)
    world.repo.restore(world.repo.dump())

    assert world.repo.get_computation_result(result.result_id) == result
    assert not world.repo.load_analysis_memo()
    world.repo.save_computation_result(result)
    application = world.repo.list_result_applications(10, "3039")[0]
    world.repo.save_result_application(application)
    with pytest.raises(ValueError, match="cannot be replaced"):
        world.repo.save_computation_result(result.model_copy(update={"error": "replacement"}))
    with pytest.raises(ValueError, match="cannot be replaced"):
        world.repo.save_result_application(application.model_copy(update={"outcome": "superseded"}))
    assert len(world.repo.list_computation_results(10, "3039")) == 1
    assert (
        len(
            [
                row
                for row in world.repo.list_result_applications(10, "3039")
                if row.phase == "collection"
            ]
        )
        == 1
    )
    assert world.repo.get_computation_result(result.result_id) == result


def test_invalid_paid_answer_keeps_usage_without_a_fabricated_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(profile="production")
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    request = world.batch.submitted[0]

    def invalid(batch_id: str, kind: CallKind = "analysis") -> Iterator[BatchResult]:
        yield BatchResult(
            custom_id=request.custom_id,
            model="gpt-5.1",
            error="invalid output",
            input_tokens=321,
            output_tokens=12,
            cache_read_input_tokens=54,
        )

    world.batch.resolve()
    with monkeypatch.context() as patch:
        patch.setattr(world.batch, "fetch_results", invalid)
        world.analysis.collect_batches()
    result = world.repo.list_computation_results(10, "3039")[0]
    assert result.validation == "error" and result.record is None
    assert result.request_sha256 == request.payload_sha256
    assert result.error == "invalid output"
    assert result.usage == TokenUsage(batch_input=321, batch_output=12, batch_cache_read=54)
    application = world.repo.list_result_applications(10, "3039")[0]
    assert application.outcome == "failed" and application.reason == "no_validated_result"
    assert world.bill("3039").analysis is None
    assert not world.repo.load_analysis_memo()


@pytest.mark.parametrize("interruption", ["status", "reset"])
def test_old_answer_does_not_become_current_after_an_operator_decision(
    interruption: Literal["status", "reset"],
) -> None:
    world = World(profile="production")
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    if interruption == "status":
        world.repo.set_status(10, "3039", BillStatus.LINKED)
    else:
        world.repo.reset_bill(10, "3039", BillStatus.ANALYSIS_PENDING)
    world.batch.resolve()

    world.analysis.collect_batches()

    assert world.bill("3039").analysis is None and world.bill("3039").ready_analysis is None
    result = world.repo.list_computation_results(10, "3039")[0]
    assert result.record is not None, "the paid output survives an obsolete target"
    application = world.repo.list_result_applications(10, "3039")[0]
    assert application.outcome == "superseded"
    assert application.reason == (
        "inactive_status" if interruption == "status" else "generation_changed"
    )
    assert not world.repo.load_analysis_memo() and not world.publisher.new_bills

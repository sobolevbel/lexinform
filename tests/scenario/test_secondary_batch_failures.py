"""Predeployment probes for restarts, partial progress and failed durable checkpoints."""

from dataclasses import replace
from datetime import timedelta

import pytest

from lexinform.models import BatchIntent, BatchResult, DeliveryPlan, JointRecord
from lexinform.services.analysis import Waiting
from lexinform.services.pipeline import RunOptions
from tests.harness import COMMITTEE_STAGES
from tests.scenario.test_secondary_batch import ready_world
from tests.scenario.test_tracking_batch import tracked_world


@pytest.mark.parametrize("workers", [1, 4])
def test_two_documents_wait_for_both_results_without_advancing_the_baseline(workers: int) -> None:
    w = tracked_world(workers=workers)
    before = w.bill("3039").observed_process
    w.run()
    first_batch = next(iter(w.batch.batches))
    w.file_to_print("3039", "Stanowisko Rządu do druku nr 3039.", suffix="002")
    w.set_stages("3039", COMMITTEE_STAGES)
    w.clock.advance(hours=1)
    w.run()
    w.batch.resolve(first_batch)

    partial = w.run()

    assert partial.batch_waiting == 1 and partial.updates == 0
    assert w.bill("3039").observed_process == before
    assert w.bill("3039").seen_supplements == ()
    assert not w.publisher.updates
    assert len(w.batch.submitted) == 2
    w.batch.resolve()

    posted = w.run()
    repeated = w.run()

    assert (posted.updates, repeated.updates) == (1, 0)
    change = w.publisher.updates[0][1]
    assert len(change.new_stages) == 2 and len(change.supplements) == 2
    assert all(record.digest is not None for record in change.supplements)
    assert not w.llm.supplement_contexts


@pytest.mark.parametrize("switch", ["disabled", "kind_removed", "provider_mismatch"])
def test_configuration_rollback_finishes_a_waiting_observation(switch: str) -> None:
    w = tracked_world()
    w.run()
    settings = w.container.settings.model_copy(deep=True)
    if switch == "disabled":
        settings.llm_batch_enabled = False
    elif switch == "kind_removed":
        settings.llm_batch_kinds = frozenset({"analysis", "reanalysis"})
    else:
        settings.llm_supplement_model = "gpt-5.1"
    restarted = replace(w.container, settings=settings)

    report = restarted.pipeline(dry_run=False).run(RunOptions(term=10))

    assert not report.errors
    assert report.updates == 1 and report.batch_waiting == 0
    assert w.bill("3039").awaiting_batch_since is None
    assert len(w.llm.supplement_contexts) == 1
    w.batch.resolve()
    late = restarted.pipeline(dry_run=False).run(RunOptions(term=10))
    assert late.updates == 0
    assert len(w.publisher.updates) == 1


def test_collection_failure_rolls_back_memo_and_consumption_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = tracked_world()
    w.run()
    w.batch.resolve()
    before = w.repo.load_analysis_memo()
    batch_id = next(iter(w.batch.batches))

    def fail_consumption(
        batch_id: str, custom_id: str, *, consumed_at: object, result: BatchResult | None = None
    ) -> None:
        raise RuntimeError("injected item checkpoint failure")

    with monkeypatch.context() as patch:
        patch.setattr(w.repo, "mark_llm_batch_item_consumed", fail_consumption)
        with pytest.raises(RuntimeError, match="item checkpoint"):
            w.analysis.collect_batches()

    assert w.repo.load_analysis_memo() == before
    assert len(w.repo.list_llm_batch_items(batch_id)) == 1
    w.repo.restore(w.repo.dump())
    restarted = replace(w.container)

    recovered = restarted.pipeline(dry_run=False).run(RunOptions(term=10))
    repeated = restarted.pipeline(dry_run=False).run(RunOptions(term=10))

    assert (recovered.updates, repeated.updates) == (1, 0)
    assert len([call for call in recovered.llm_calls if call.kind == "supplement"]) == 1
    assert not [call for call in repeated.llm_calls if call.kind == "supplement"]
    assert not w.llm.supplement_contexts


def test_delivery_checkpoint_failure_preserves_wait_marker_and_collected_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = tracked_world()
    w.run()
    since = w.bill("3039").awaiting_batch_since
    before = w.bill("3039").observed_process
    w.batch.resolve()

    def fail_delivery(change_id: int, channel_id: str, delivery: DeliveryPlan) -> None:
        raise RuntimeError("injected delivery checkpoint failure")

    with monkeypatch.context() as patch:
        patch.setattr(w.repo, "save_update_delivery", fail_delivery)
        failed = w.run()

    assert failed.errors
    assert w.bill("3039").awaiting_batch_since == since
    assert w.bill("3039").observed_process == before
    assert not w.publisher.updates
    w.repo.restore(w.repo.dump())
    w.clock.advance(hours=1)

    recovered = w.run(since=w.clock.now() + timedelta(days=1))

    assert recovered.updates == 1
    assert w.bill("3039").awaiting_batch_since is None
    assert not w.llm.supplement_contexts
    assert not [call for call in recovered.llm_calls if call.kind == "supplement"]


def test_failed_intent_write_releases_the_budget_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = ready_world()
    settings = w.container.settings.model_copy(update={"max_run_cost_usd": 0.15})
    analysis = replace(w.container, settings=settings).analysis_service()
    analysis.start_run()

    def fail_intent(intent: BatchIntent) -> None:
        raise RuntimeError("injected intent failure")

    with monkeypatch.context() as patch:
        patch.setattr(w.repo, "save_batch_intent", fail_intent)
        with pytest.raises(RuntimeError, match="intent failure"):
            analysis.compare_joint(w.bill("3040"), [w.bill("3039")], may_wait=True)

    answer = analysis.compare_joint(w.bill("3040"), [w.bill("3039")], may_wait=True)

    assert isinstance(answer, Waiting)
    assert len(w.repo.list_queued_batch_intents()) == 1
    assert not w.llm.joint_contexts


def test_wrong_secondary_answer_type_falls_back_instead_of_poisoning_the_memo() -> None:
    w = ready_world()
    w.analysis.compare_joint(w.bill("3040"), [w.bill("3039")], may_wait=True)
    w.analysis.submit_queued_batches()
    record = w.bill("3039").analysis
    assert record is not None
    w.batch.script["3040"] = record.analysis
    w.batch.resolve()
    w.analysis.collect_batches()

    answer = w.analysis.compare_joint(w.bill("3040"), [w.bill("3039")], may_wait=True)

    assert isinstance(answer, JointRecord)
    assert len(w.llm.joint_contexts) == 1

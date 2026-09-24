"""Executable acceptance criteria for unresolved secondary batch audit findings."""

from dataclasses import replace
from datetime import timedelta

import pytest

from lexinform.models import Stage
from lexinform.services.analysis import Waiting
from lexinform.services.pipeline import RunOptions
from tests.harness import COMMITTEE_STAGES
from tests.scenario.test_joint_batch import joint_world
from tests.scenario.test_secondary_batch import ask, ready_world
from tests.scenario.test_tracking_batch import tracked_world


@pytest.mark.xfail(strict=True, reason="B45: a refused reservation falls through to paid sync")
def test_exhausted_secondary_budget_does_not_start_a_synchronous_call() -> None:
    w = ready_world()
    settings = w.container.settings.model_copy(update={"max_run_cost_usd": 0.001})
    analysis = replace(w.container, settings=settings).analysis_service()
    analysis.start_run()

    analysis.compare_joint(w.bill("3040"), [w.bill("3039")], may_wait=True)

    assert not w.llm.joint_contexts


@pytest.mark.parametrize("switch", ["disabled", "kind_removed"])
@pytest.mark.xfail(strict=True, reason="B46: saved queued requests bypass submission switches")
def test_queued_secondary_respects_the_current_submission_switch(switch: str) -> None:
    w = ready_world()
    assert isinstance(ask(w), Waiting)
    settings = w.container.settings.model_copy(deep=True)
    if switch == "disabled":
        settings.llm_batch_enabled = False
    else:
        settings.llm_batch_kinds = frozenset({"analysis", "reanalysis"})
    analysis = replace(w.container, settings=settings).analysis_service()
    analysis.start_run()

    analysis.submit_queued_batches()

    assert not w.batch.submitted


@pytest.mark.xfail(strict=True, reason="B47: supplement waiting hides an urgent new hearing")
def test_new_hearing_deadline_is_not_held_behind_a_supplement() -> None:
    w = tracked_world()
    hearing = Stage(
        stage_name="Wysłuchanie publiczne",
        stage_type="PublicHearing",
        date=w.clock.now().date() + timedelta(days=10),
    )
    w.set_stages("3039", COMMITTEE_STAGES + (hearing,))

    report = w.run()

    assert not report.errors
    assert report.hearing_reminders == 1


@pytest.mark.xfail(strict=True, reason="B48: collected secondary answer still needs its source")
def test_collected_supplement_survives_a_missing_source_document() -> None:
    w = tracked_world()
    w.run()
    w.batch.resolve()
    w.gateway.files.clear()
    w.repo.restore(w.repo.dump())
    restarted = replace(w.container)

    report = restarted.pipeline(dry_run=False).run(RunOptions(term=10))

    assert report.updates == 1
    assert w.publisher.updates[0][1].supplements[0].digest is not None


@pytest.mark.xfail(strict=True, reason="B49: changing joint context resets the reply deadline")
def test_joint_deadline_survives_a_changed_comparison_context() -> None:
    w = joint_world()
    w.run()
    w.clock.advance(hours=7)
    old = w.bill("1933").analysis
    assert old is not None
    new = old.model_copy(
        update={"analysis": old.analysis.model_copy(update={"summary": "Updated description"})}
    )
    w.repo.save_analysis(10, "1933", new)

    report = w.run()

    assert report.joint_published == 1


def test_waiting_supplement_survives_the_tracking_age_cutoff() -> None:
    w = tracked_world()
    w.touch(
        "3039",
        w.clock.now(),
        closure_date=w.clock.now().date()
        - timedelta(days=w.container.settings.track_closed_grace_days),
        passed=False,
    )
    waiting = w.run()
    assert waiting.batch_waiting == 1
    w.clock.advance(days=1)
    w.batch.resolve()

    report = w.run()

    assert report.updates == 1
    assert w.bill("3039").awaiting_batch_since is None

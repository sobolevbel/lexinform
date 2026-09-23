"""Open review candidates; --runxfail exposes the expected-behaviour assertions."""

import datetime as dt
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lexinform.adapters.batch_checkpoint import GitBatchCheckpoint
from lexinform.cli import app
from lexinform.errors import BatchNotSubmittedError, LlmUnavailableError
from lexinform.models import BatchRequest, BatchStatus, BillStatus
from lexinform.pricing import cost_usd
from lexinform.services.pipeline import RunOptions
from tests.fakes import FakeBatchBackend, FakeTextExtractor, make_analysis
from tests.harness import RCL, TERM, World, rcl_document, rcl_folder, rcl_stage
from tests.scenario.test_tracking import REPORT_TEXT, REPORT_URL, WITH_REPORT


def test_submit_failure_keeps_work_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    w = World(batch=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    def unavailable(requests: Sequence[BatchRequest]) -> str:
        raise BatchNotSubmittedError("injected rejection before remote acceptance")

    with monkeypatch.context() as patch:
        patch.setattr(w.batch, "submit", unavailable)
        failed = w.run()
    assert failed.errors
    assert not w.repo.list_open_llm_batches()

    w.run()

    assert len(w.batch.submitted) == 1, w.bill("3039").status


def test_uncertain_submission_waits_for_explicit_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = World(batch=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    def uncertain(requests: Sequence[BatchRequest]) -> str:
        raise LlmUnavailableError("connection lost after request was sent")

    with monkeypatch.context() as patch:
        patch.setattr(w.batch, "submit", uncertain)
        w.run()
    pending = w.repo.list_submitting_batch_intents()
    assert len(pending) == 1

    w.run()

    assert not w.batch.submitted
    assert w.repo.list_submitting_batch_intents() == pending


def test_rcl_reanalysis_is_not_submitted_twice_while_in_flight() -> None:
    w = World(batch=True)
    project = w.add_rcl_project()
    w.run()
    w.batch.resolve()
    w.run()
    w.clock.advance(days=1)
    text = rcl_folder(
        777, "Projekt", rcl_document(801, "projekt_po_KP.pdf", created=dt.date(2026, 9, 8))
    )
    w.add_rcl_project(
        project.model_copy(
            update={
                "stages": (
                    *project.stages[:4],
                    rcl_stage(9, "Stały Komitet Rady Ministrów", "reached"),
                    rcl_stage(10, "Komisja Prawnicza", "active", text),
                    *project.stages[5:],
                ),
                "modified": dt.date(2026, 9, 8),
            }
        )
    )

    w.run(full_track=True)
    before = len(w.batch.submitted)
    w.run(full_track=True)

    assert len(w.batch.submitted) == before, w.bill(RCL).status


def test_collect_respects_an_operator_skip() -> None:
    w = World(batch=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.command("/skip 3039")
    w.run()
    assert w.bill("3039").status is BillStatus.SKIPPED_PREFILTER

    w.batch.resolve()
    w.run()

    assert not w.publisher.new_bills
    assert w.bill("3039").status is BillStatus.SKIPPED_PREFILTER


def test_old_result_cannot_complete_a_new_job_after_reset() -> None:
    w = World(batch=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.repo.reset_bill(TERM, "3039", BillStatus.ANALYSIS_PENDING)
    w.run()
    assert len(w.batch.batches) == 2

    w.batch.resolve("batch-1")
    report = w.run()

    assert w.bill("3039").status is BillStatus.BATCH_PENDING
    assert not w.publisher.new_bills
    assert report.llm_input_tokens == 100
    w.batch.resolve("batch-2")
    w.run()
    assert len(w.publisher.new_bills) == 1


def test_completed_initial_analysis_uses_its_saved_input_without_downloading_again() -> None:
    w = World(batch=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.batch.resolve()
    w.gateway.outages.add("download")
    restarted = replace(w.container)

    report = restarted.pipeline(dry_run=False).run(RunOptions(term=TERM, track=False))

    assert report.analyzed == 1
    assert len(w.batch.submitted) == 1
    assert len(w.publisher.new_bills) == 1


def test_submission_requires_a_successful_external_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = World(batch=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    restarted = replace(
        w.container,
        settings=w.container.settings.model_copy(
            update={"llm_batch_state_file": Path("state.sql")}
        ),
    )

    def refuse_checkpoint(checkpoint: GitBatchCheckpoint) -> None:
        assert w.repo.list_submitting_batch_intents()
        raise RuntimeError("state push rejected")

    monkeypatch.setattr(GitBatchCheckpoint, "__call__", refuse_checkpoint)
    report = restarted.pipeline(dry_run=False).run(RunOptions(term=TERM))

    assert report.errors
    assert not w.batch.submitted
    assert w.repo.list_submitting_batch_intents()


def test_collect_does_not_pay_for_triage_again() -> None:
    w = World(batch=True, triage=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    assert len(w.llm.triage_contexts) == 1

    w.batch.resolve()
    w.run()

    assert len(w.llm.triage_contexts) == 1


def test_batch_submission_obeys_the_run_budget() -> None:
    w = World(batch=True, max_run_cost_usd=0.000001)
    for number in ("3039", "3040", "3041"):
        w.add_bill(number, "Projekt ustawy o cudzoziemcach")

    report = w.run()

    assert len(w.batch.submitted) <= 1, (len(w.batch.submitted), report.notes)


def test_collected_reanalysis_is_applied_even_after_discovery_watermark_moves() -> None:
    w = World(batch=True, extractor=FakeTextExtractor(by_content={b"%PDF-report": REPORT_TEXT}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.batch.resolve()
    w.run()
    w.clock.advance(days=1)
    w.touch("3039", w.clock.now())
    w.set_stages("3039", WITH_REPORT)
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    w.run(full_track=True)
    w.clock.advance(days=1)
    w.run(since=None)
    w.clock.advance(days=1)
    w.run(since=None)
    w.clock.advance(days=1)
    w.batch.resolve()

    report = w.run(since=None)

    assert report.reanalyzed == 1, (report.tracked, w.bill("3039").status)


def test_ready_reanalysis_survives_restart_and_a_missing_document() -> None:
    w = World(batch=True, extractor=FakeTextExtractor(by_content={b"%PDF-report": REPORT_TEXT}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.batch.resolve()
    w.run()
    w.set_stages("3039", WITH_REPORT)
    w.gateway.files[REPORT_URL] = b"%PDF-report"
    w.run(full_track=True)
    w.batch.resolve()
    w.gateway.outages.add("download")
    restarted = replace(w.container)

    report = restarted.pipeline(dry_run=False).run(RunOptions(term=TERM, full_track=True))

    assert report.reanalyzed == 1
    assert w.bill("3039").ready_analysis is None
    assert len(w.batch.submitted) == 2


def test_dry_run_still_previews_a_new_analysis_without_submitting_a_batch() -> None:
    w = World(batch=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    report = w.container.pipeline(dry_run=True).run(RunOptions(dry_run=True, term=TERM))

    assert not w.batch.submitted
    assert report.analyzed == 1, report


def test_failed_batch_remains_collectible_after_interrupted_item_consumption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = World(batch=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()

    def failed(batch_id: str) -> str:
        return "failed"

    def interrupted(term: int, number: str, error: str) -> None:
        raise RuntimeError("injected crash after batch marked failed")

    with monkeypatch.context() as patch:
        patch.setattr(w.batch, "poll", failed)
        patch.setattr(w.repo, "record_analysis_failure", interrupted)
        w.run(expect_bugs=True)

    assert w.repo.list_open_llm_batches(), w.bill("3039").status


class _OtherProvider(FakeBatchBackend):
    def __init__(self) -> None:
        super().__init__()
        self.polled: list[str] = []

    def poll(self, batch_id: str) -> BatchStatus:
        self.polled.append(batch_id)
        return "failed"


def test_switching_provider_does_not_poll_old_ids_on_the_new_provider() -> None:
    w = World(batch=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    other = _OtherProvider()
    restarted = replace(
        w.container,
        settings=w.container.settings.model_copy(update={"llm_batch_provider": "openai"}),
        batch_override=other,
    )

    restarted.analysis_service().collect_batches()

    assert not other.polled


def test_collected_tokens_are_in_the_run_totals() -> None:
    w = World(batch=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.batch.resolve()

    report = w.run()

    assert report.llm_calls and report.llm_calls[0].input_tokens > 0
    assert report.llm_input_tokens == sum(call.input_tokens for call in report.llm_calls)
    assert report.llm_calls[0].batched
    assert cost_usd(report.llm_usage) == cost_usd(report.llm_calls[0].usage)


def test_government_batch_survives_a_term_rollover() -> None:
    w = World(batch=True)
    w.add_rcl_project()
    w.run()
    assert w.repo.move_government_rows(TERM, TERM + 1) == 0
    w.batch.resolve()

    w.run()
    assert w.bill(RCL).status is BillStatus.ANALYZED
    assert len(w.batch.submitted) == 1
    assert w.repo.move_government_rows(TERM, TERM + 1) == 1

    moved = w.repo.get(TERM + 1, RCL)
    assert moved is not None
    assert moved.status is BillStatus.ANALYZED


def test_collect_cli_respects_configured_min_score(monkeypatch: pytest.MonkeyPatch) -> None:
    w = World(batch=True, batch_script={"3039": make_analysis(score=3)})
    w.container.settings.min_score = 5
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run(min_score=5)
    w.batch.resolve()
    monkeypatch.setattr("lexinform.cli._container", lambda: w.container)

    result = CliRunner().invoke(app, ["collect-batches"])

    assert result.exit_code == 0, result.output
    assert not w.publisher.new_bills


@pytest.mark.parametrize("workers", [1, 4])
def test_open_batch_reserves_budget_after_a_restart(workers: int) -> None:
    w = World(batch=True, max_run_cost_usd=0.15)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.add_bill("3040", "Projekt ustawy o cudzoziemcach")
    restarted = replace(
        w.container,
        settings=w.container.settings.model_copy(update={"llm_concurrency": workers}),
    )

    report = restarted.pipeline(dry_run=False).run(RunOptions(term=TERM))

    assert len(w.batch.submitted) == 1
    assert w.bill("3040").status is BillStatus.ANALYSIS_PENDING
    assert any("run cost limit reached" in note for note in report.notes)
    w.batch.resolve()
    restarted.pipeline(dry_run=False).run(RunOptions(term=TERM))
    assert len(w.batch.submitted) == 2


def test_a_batch_request_larger_than_the_remaining_budget_is_not_submitted() -> None:
    w = World(batch=True, max_run_cost_usd=0.01)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    report = w.run()

    assert not w.batch.submitted
    assert w.bill("3039").status is BillStatus.ANALYSIS_PENDING
    assert any("run cost limit reached" in note for note in report.notes)

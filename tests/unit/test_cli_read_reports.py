import datetime as dt
from typing import get_args

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import (
    CliReadOperation,
    CommandOutcome,
    DocumentDownloadDiagnostics,
    DocumentReadMetrics,
    IncomingCommand,
    MonthFigures,
    OutcomeStatus,
    RunMode,
    RunReport,
    StatusSnapshot,
    TokenUsage,
    WeekFigures,
)
from tests.formatting import assert_telegram_html

NOW = dt.datetime(2026, 10, 28, tzinfo=dt.UTC)


def cli_report() -> RunReport:
    return RunReport(
        started_at=NOW,
        since=NOW,
        finished_at=NOW,
        mode=RunMode.CLI_READS,
        cli_read_operation="reprefilter",
        document_download_diagnostics=DocumentDownloadDiagnostics(invocations=2, completed=2),
    )


@pytest.mark.parametrize("operation", get_args(CliReadOperation))
def test_reader_accepts_the_finite_cli_operation_without_execution_accounting(
    operation: CliReadOperation,
) -> None:
    report = cli_report().model_copy(update={"cli_read_operation": operation})
    restored = RunReport.model_validate_json(report.model_dump_json())

    assert restored == report and not restored.execution_accounting_measured


@pytest.mark.parametrize("mutation", ["missing", "other-mode", "unknown-operation"])
def test_cli_operation_cannot_be_missing_or_attached_to_another_scope(mutation: str) -> None:
    value = cli_report().model_dump(mode="json")
    if mutation == "missing":
        value.pop("cli_read_operation")
    elif mutation == "other-mode":
        value["mode"] = "run"
    else:
        value["cli_read_operation"] = "show"
    with pytest.raises(ValidationError):
        RunReport.model_validate(value)


def test_historical_report_json_omits_the_new_scope_field() -> None:
    report = RunReport(started_at=NOW, since=NOW, mode=RunMode.RUN)
    raw = report.model_dump_json()

    assert "cli_read_operation" not in raw
    assert report.execution_accounting_measured
    assert RunReport.model_validate_json(raw).model_dump_json() == raw


def test_operation_labels_and_zero_measurements_do_not_make_an_active_read_report() -> None:
    report = cli_report().model_copy(
        update={
            "document_download_diagnostics": DocumentDownloadDiagnostics(),
            "document_reads": DocumentReadMetrics(),
        }
    )

    assert report.is_empty
    assert not report.model_copy(
        update={"document_download_diagnostics": DocumentDownloadDiagnostics(cache_hits=1)}
    ).is_empty


@pytest.mark.parametrize("mode", [RunMode.RUN, RunMode.TRACK, RunMode.COLLECT])
def test_read_measurements_alone_do_not_make_an_execution_run_a_result(mode: RunMode) -> None:
    idle = RunReport(
        started_at=NOW,
        since=NOW,
        mode=mode,
        document_reads=DocumentReadMetrics(cache_hits=3),
        document_download_diagnostics=DocumentDownloadDiagnostics(invocations=1, completed=1),
    )

    assert idle.is_empty
    assert not idle.model_copy(update={"discovered": 1}).is_empty


def test_current_reader_restores_future_reports_without_moving_the_watermark(
    repo: SqliteBillRepository,
) -> None:
    full = RunReport(
        started_at=NOW - dt.timedelta(days=2),
        since=NOW - dt.timedelta(days=2),
        finished_at=NOW,
        mode=RunMode.RUN,
        discovery_ok=True,
    )
    full_id = repo.start_run(full)
    repo.finish_run(full_id, full)
    cli = cli_report().model_copy(update={"discovery_ok": True})
    cli_id = repo.start_run(cli)
    repo.finish_run(cli_id, cli)
    dump = repo.dump()
    restored = SqliteBillRepository(":memory:")
    try:
        restored.restore(dump)
        assert restored.dump() == dump
        assert restored.list_runs(since=full.started_at) == [cli, full]
        assert restored.last_discovery_started_at() == full.started_at
        assert restored.prune_runs(before=NOW + dt.timedelta(days=1)) == 1
        assert restored.list_runs(since=full.started_at) == [full]
    finally:
        restored.close()


def test_diagnostic_defaults_are_not_execution_figures_or_usage() -> None:
    full = RunReport(
        started_at=NOW,
        since=NOW,
        mode=RunMode.RUN,
        discovered=3,
        published=1,
        prefilter_rejected=[],
        llm_usage={"gpt-5.1": TokenUsage(input=100)},
    )
    cli = cli_report().model_copy(
        update={
            "discovered": 999,
            "published": 999,
            "prefilter_rejected": None,
            "llm_usage": {"gpt-5.1": TokenUsage(input=999_999)},
        }
    )

    assert MonthFigures.of(NOW.date(), [cli, full]) == MonthFigures.of(NOW.date(), [full])
    assert WeekFigures.of([cli, full]) == WeekFigures.of([full])


def test_all_report_views_name_the_measured_scope_without_zero_accounting() -> None:
    report = cli_report()
    formatter = MessageFormatter("en")
    incoming = IncomingCommand(
        update_id=1, chat_id="@test", message_id=1, text="/runs all", received_at=NOW
    )
    full = formatter.run_report(report, [])
    listed = formatter.command_reply(
        incoming, CommandOutcome(status=OutcomeStatus.LISTED, runs=(report,))
    )
    for rendered in (full, listed):
        assert "reprefilter" in rendered.text and "unmeasured" in rendered.text
        assert "completed: 2" in rendered.text
        assert all(
            text not in rendered.text
            for text in ("$0", "0/0", "nothing analyzed", "nothing posted", "disc·anal·publ·upd")
        )
        assert_telegram_html(rendered.text)
    status = formatter.command_reply(
        incoming,
        CommandOutcome(status=OutcomeStatus.REPORTED, snapshot=StatusSnapshot(runs=(report,))),
    )
    assert "runs</b>: none" in status.text and "reprefilter" not in status.text

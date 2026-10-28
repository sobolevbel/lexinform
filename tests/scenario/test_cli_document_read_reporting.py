import datetime as dt

import pytest
from typer.testing import CliRunner

from lexinform import cli
from lexinform.container import Container
from lexinform.log_context import current_log_fields
from lexinform.models import Bill, BillRef, BillStatus, RunMode, RunReport
from lexinform.services.lookup import BillNotFoundError
from lexinform.settings import Settings
from tests.harness import RCL, TERM, World, print_url

TITLE = "Projekt ustawy o cudzoziemcach"
CLI_ENV = {"LEXINFORM_TELEGRAM_BOT_TOKEN": "", "LEXINFORM_TELEGRAM_LOG_CHANNEL_ID": ""}


def use_container(monkeypatch: pytest.MonkeyPatch, world: World) -> list[RunReport]:
    closed: list[RunReport] = []

    def build(settings: Settings) -> Container:
        return world.container

    def close() -> None:
        closed.extend(world.repo.list_runs(since=world.clock.now() - dt.timedelta(days=1)))

    monkeypatch.setattr(cli, "build_container", build)
    monkeypatch.setattr(world.container, "close", close)
    return closed


@pytest.mark.parametrize(
    "args",
    [
        ["analyze", "4100"],
        ["explain", "4100"],
        ["amendments", "4100"],
        ["republish", "4100", "--yes"],
        ["forget", "4100", "--yes"],
        ["reset", "4100", "--yes"],
        ["observe", "4100", "--mode", "off", "--yes"],
    ],
)
def test_standalone_root_registers_before_lookup_and_snapshots_before_close(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    world = World()
    world.add_bill("4100", TITLE)
    closed = use_container(monkeypatch, world)
    lookup = world.container.bill_lookup()

    def load(number: str) -> Bill:
        assert type(current_log_fields().get("run_id")) is int
        assert not world.repo.list_runs(since=world.clock.now())
        assert "'cli_reads'" in world.repo.dump()
        world.container.text_loader().read(print_url("4100"))
        raise BillNotFoundError("lookup stopped after read")

    def read(ref: BillRef) -> Bill:
        return load(ref.value)

    monkeypatch.setattr(lookup, "load", load)
    monkeypatch.setattr(lookup, "read_ref", read)

    result = CliRunner().invoke(cli.app, args, env=CLI_ENV)

    assert result.exit_code == (2 if args[0] == "explain" else 1)
    assert "lookup stopped after read" in result.output
    assert len(closed) == 1 and closed[0].cli_read_operation == args[0]
    assert closed[0].mode is RunMode.CLI_READS and closed[0].term is None
    observed = closed[0].document_download_diagnostics
    assert observed is not None, "The standalone root snapshots the lookup's file read"
    assert observed.completed == 1 and len(world.repo.list_artifact_reads()) == 1
    assert not world.llm.contexts and not world.batch.submitted
    assert not world.publisher.sent and not world.notifier.calls
    assert current_log_fields() == {}


@pytest.mark.parametrize("command", ["scan", "reprefilter"])
def test_discovery_roots_have_one_owner_without_running_a_daily_report(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o podatku VAT")
    if command == "reprefilter":
        world.repo.upsert_summary(world.gateway.processes[0], now=world.clock.now())
        world.repo.set_status(TERM, "4100", BillStatus.SKIPPED_PREFILTER)
    closed = use_container(monkeypatch, world)

    result = CliRunner().invoke(cli.app, [command], env=CLI_ENV)

    assert result.exit_code == 0, result.output
    assert len(closed) == 1 and closed[0].cli_read_operation == command
    assert closed[0].mode is RunMode.CLI_READS and closed[0].term is None
    assert closed[0].document_reads is not None, "The root owns its final monitor snapshot"
    assert not world.llm.contexts and not world.batch.submitted and not world.publisher.sent
    assert not world.notifier.calls


def test_disabled_prefilter_and_successful_early_exit_keep_their_original_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disabled = World(text_prefilter=False)
    closed = use_container(monkeypatch, disabled)
    result = CliRunner().invoke(cli.app, ["reprefilter"], env=CLI_ENV)
    assert result.exit_code == 2 and "text prefilter is disabled" in result.output
    assert len(closed) == 1 and closed[0].cli_read_operation == "reprefilter"
    assert closed[0].is_empty and not closed[0].errors

    world = World()
    world.add_bill("4100", TITLE)
    world.repo.upsert_summary(world.gateway.processes[0], now=world.clock.now())
    closed = use_container(monkeypatch, world)
    result = CliRunner().invoke(cli.app, ["forget", "4100", "--yes"], env=CLI_ENV)
    assert result.exit_code == 0 and "nothing to forget" in result.output
    assert len(closed) == 1 and closed[0].is_empty and not closed[0].errors


@pytest.mark.parametrize(("args", "exit_code"), [(["show", "4100"], 0), (["preview", "4100"], 2)])
def test_read_only_cli_roots_add_no_capture_or_sql_writes(
    monkeypatch: pytest.MonkeyPatch, args: list[str], exit_code: int
) -> None:
    world = World()
    world.add_bill("4100", TITLE)
    world.repo.upsert_summary(world.gateway.processes[0], now=world.clock.now())
    use_container(monkeypatch, world)
    before = world.repo.dump()

    result = CliRunner().invoke(cli.app, args, env=CLI_ENV)

    assert result.exit_code == exit_code, result.output
    assert world.repo.dump() == before
    assert not world.repo.list_runs(since=world.clock.now())


def test_real_initial_rcl_lookup_is_captured_before_a_later_early_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World()
    world.add_rcl_project()
    closed = use_container(monkeypatch, world)

    result = CliRunner().invoke(cli.app, ["republish", RCL, "--yes"], env=CLI_ENV)

    assert result.exit_code == 2 and "no relevant analysis" in result.output
    assert len(closed) == 1 and closed[0].cli_read_operation == "republish"
    observed = closed[0].document_download_diagnostics
    assert observed is not None, "Initial RCL lookup reads the consultation letter"
    assert observed.completed > 0 and world.repo.list_artifact_reads()
    assert not world.llm.contexts and not world.batch.submitted and not world.publisher.sent


def test_prefilter_outage_is_reported_without_spending_a_bill_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o podatku VAT")
    world.repo.upsert_summary(world.gateway.processes[0], now=world.clock.now())
    world.repo.set_status(TERM, "4100", BillStatus.SKIPPED_PREFILTER)
    world.gateway.outages.add("download")
    closed = use_container(monkeypatch, world)

    result = CliRunner().invoke(cli.app, ["reprefilter"], env=CLI_ENV)

    assert result.exit_code == 0, result.output
    assert len(closed) == 1
    observed = closed[0].document_download_diagnostics
    assert observed is not None, "The root records the loader outage in its finally"
    assert observed.invocations == observed.source_unavailable == 1
    assert world.bill("4100").analysis_attempts == 0 and not world.repo.list_artifact_reads()
    assert not world.llm.contexts and not world.batch.submitted


def test_diagnostic_persistence_failure_preserves_cli_success_and_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World()
    world.add_bill("4100", TITLE)
    world.repo.upsert_summary(world.gateway.processes[0], now=world.clock.now())
    use_container(monkeypatch, world)

    def finish(run_id: int, report: RunReport) -> None:
        raise OSError("SECRET diagnostics error")

    monkeypatch.setattr(world.repo, "finish_run", finish)
    result = CliRunner().invoke(cli.app, ["forget", "4100", "--yes"], env=CLI_ENV)

    assert result.exit_code == 0 and "nothing to forget" in result.stdout
    assert "SECRET" not in result.output
    assert not world.repo.list_runs(since=world.clock.now())


def test_inbox_pipeline_keeps_its_existing_owner_only() -> None:
    world = World()
    world.command("/show 4100")

    report = world.run(
        mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0
    )

    assert world.repo.list_runs(since=report.started_at) == [report]
    assert report.mode is RunMode.COMMANDS and report.cli_read_operation is None

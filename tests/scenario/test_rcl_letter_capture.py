import datetime as dt
import threading
from typing import Never

import pytest
from typer.testing import CliRunner

from lexinform import cli
from lexinform.errors import StateCheckpointError
from lexinform.models import RclLetterUse
from lexinform.services import pipeline
from lexinform.services.pipeline import DailyPipeline, RunOptions
from tests.harness import RCL, SINCE, TERM, World, print_url
from tests.scenario.test_cli_document_read_reporting import CLI_ENV, use_container
from tests.unit.test_document_read_reporting import OpaqueHistory


@pytest.mark.parametrize("workers", [1, 4])
def test_shared_reader_collects_before_enrollment_and_pipeline_flushes_on_caller(
    monkeypatch: pytest.MonkeyPatch,
    workers: int,
) -> None:
    world = World(workers=workers)
    project = world.add_rcl_project()
    before = world.repo.dump()
    first = world.container.rcl_reader().consultation(project)
    second = world.container.rcl_reader().consultation(project)
    assert first == second and world.repo.dump() == before
    assert world.repo.get(TERM, RCL) is None
    assert len(world.container.rcl_letter_uses().read_rcl_letter_uses()) == 1
    caller = threading.get_ident()
    threads: list[int] = []
    save = world.repo.save_rcl_letter_use

    def record(use: RclLetterUse, *, recorded_at: dt.datetime) -> None:
        threads.append(threading.get_ident())
        save(use, recorded_at=recorded_at)

    monkeypatch.setattr(world.repo, "save_rcl_letter_use", record)
    world.run(track=False)
    records = list(world.repo.iter_rcl_letter_uses(project.id))
    assert len(records) == 1 and threads == [caller]
    assert world.repo.get(TERM, RCL) is not None
    first_time = records[0].recorded_at
    world.clock.advance(days=1)
    world.run(discover=False, max_analyze=0, publish=False, track=False, digest=False)
    assert list(world.repo.iter_rcl_letter_uses(project.id))[0].recorded_at == first_time
    assert len(world.container.rcl_letter_uses().read_rcl_letter_uses()) == 1


@pytest.mark.parametrize(
    "failure", [StateCheckpointError("business failure"), KeyboardInterrupt("business interrupted")]
)
def test_pipeline_new_hook_failure_keeps_original_business_exception_and_atomic_policy(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    world = World()
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    loader = world.container.text_loader()
    loader.read(print_url("4100"))
    history = OpaqueHistory(KeyboardInterrupt("private history prose"))

    def fail_discover(term: int, since: dt.datetime, *, pre_print: bool = True) -> Never:
        raise failure

    def fail_log(*args: object, **kwargs: object) -> None:
        raise RuntimeError("logging interrupted")

    monkeypatch.setattr(world.discovery, "discover", fail_discover)
    monkeypatch.setattr(pipeline.log, "warning", fail_log)
    run = DailyPipeline(
        world.repo,
        world.discovery,
        world.analysis,
        world.container.publishing_service(dry_run=False),
        world.tracking,
        world.clock,
        terms=world.container.terms,
        document_artifacts=loader,
        document_file_reads=loader,
        source_history=history,
    )
    with pytest.raises(type(failure)) as caught:
        run.run(
            RunOptions(
                since=SINCE,
                term=TERM,
                discover=True,
                rcl=False,
                wykaz=False,
                max_analyze=0,
                publish=False,
                track=False,
                digest=False,
            )
        )
    assert caught.value is failure
    assert history.calls == [(threading.get_ident(), world.clock.now())]
    assert not world.repo.list_artifact_reads()
    assert world.repo.get_artifact_metadata("e" * 64) is None
    assert not world.repo.list_runs(since=SINCE)


def test_dry_run_rolls_back_capture_without_forgetting_known_observation() -> None:
    world = World()
    project = world.add_rcl_project()
    before = world.repo.dump()
    world.run(dry_run=True, track=False)
    assert world.repo.dump() == before
    assert world.container.rcl_letter_uses().read_rcl_letter_uses()
    assert not list(world.repo.iter_rcl_letter_uses(project.id))
    with world.container.cli_document_reads().capture("scan"):
        pass
    assert list(world.repo.iter_rcl_letter_uses(project.id))


@pytest.mark.parametrize("command", ["show", "preview"])
@pytest.mark.parametrize("known", [False, True])
def test_read_roots_keep_transient_capture_without_a_new_history_owner(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    known: bool,
) -> None:
    world = World()
    project = world.add_rcl_project()
    if known:
        world.container.bill_lookup().load(RCL)
    before = world.repo.dump()
    closed = use_container(monkeypatch, world)
    result = CliRunner().invoke(cli.app, [command, RCL], env=CLI_ENV)
    assert result.exit_code == (0 if command == "show" else 2), result.output
    assert world.container.rcl_letter_uses().read_rcl_letter_uses()
    assert not list(world.repo.iter_rcl_letter_uses(project.id)) and not closed
    if command == "preview" and not known:
        assert world.repo.get(TERM, RCL) is not None
    else:
        assert world.repo.dump() == before


def test_standalone_analyze_owns_real_unknown_rcl_lookup_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World()
    project = world.add_rcl_project()
    closed = use_container(monkeypatch, world)
    result = CliRunner().invoke(cli.app, ["analyze", RCL], env=CLI_ENV)
    assert result.exit_code == 0, result.output
    assert len(closed) == 1 and closed[0].cli_read_operation == "analyze"
    assert len(list(world.repo.iter_rcl_letter_uses(project.id))) == 1

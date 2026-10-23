import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from lexinform.adapters.batch_checkpoint import GitBatchCheckpoint
from lexinform.adapters.checkpoint import NoopStateCheckpoint
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.container import Container
from lexinform.errors import StateCheckpointError
from lexinform.keywords import KeywordPrefilter
from lexinform.services.pipeline import RunOptions
from lexinform.services.terms import TermResolver
from lexinform.settings import Settings
from tests.fakes import FakeSejmGateway, FixedClock
from tests.harness import TERM, World


def container(settings: Settings) -> Container:
    repo = SqliteBillRepository(":memory:")
    repo.migrate()
    gateway = FakeSejmGateway()
    return Container(
        settings=settings,
        clock=FixedClock(),
        repo=repo,
        gateway=gateway,
        formatter=MessageFormatter("ru"),
        prefilter=KeywordPrefilter(),
        terms=TermResolver(gateway, repo),
    )


def git_directory(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "state", str(path)], check=True, capture_output=True)
    remote = path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", str(remote)],
        check=True,
        capture_output=True,
    )


def test_disconnected_local_container_has_one_noop_checkpoint() -> None:
    c = container(Settings(_env_file=None))

    checkpoint = c.state_checkpoint()
    checkpoint()

    assert isinstance(checkpoint, NoopStateCheckpoint)
    assert c.state_checkpoint() is checkpoint
    assert c.analysis_options().require_batch


def test_required_checkpoint_refuses_a_missing_file_before_building_a_reader_publisher() -> None:
    c = container(Settings(_env_file=None, state_checkpoint_required=True))

    with pytest.raises(StateCheckpointError, match="LEXINFORM_LLM_BATCH_STATE_FILE"):
        c.publisher(dry_run=False)
    with pytest.raises(StateCheckpointError, match="LEXINFORM_LLM_BATCH_STATE_FILE"):
        c.analysis_service()


@pytest.mark.parametrize("branch", ["", " ", "invalid branch", "-state", "state..other"])
def test_required_checkpoint_refuses_an_invalid_branch_before_external_work(
    tmp_path: Path, branch: str
) -> None:
    git_directory(tmp_path)
    output = tmp_path / "state.sql"
    c = container(
        Settings(
            _env_file=None,
            state_checkpoint_required=True,
            llm_batch_state_file=output,
            llm_batch_state_branch=branch,
        )
    )

    with pytest.raises(StateCheckpointError, match="branch"):
        c.publisher(dry_run=False)

    assert not output.exists()


def test_required_checkpoint_refuses_a_directory_instead_of_a_sql_file(tmp_path: Path) -> None:
    c = container(
        Settings(_env_file=None, state_checkpoint_required=True, llm_batch_state_file=tmp_path)
    )

    with pytest.raises(StateCheckpointError, match="SQL file path"):
        c.state_checkpoint()


def test_checkpoint_refuses_a_non_git_directory_before_external_work(tmp_path: Path) -> None:
    c = container(
        Settings(
            _env_file=None,
            state_checkpoint_required=True,
            llm_batch_state_file=tmp_path / "state.sql",
        )
    )

    with pytest.raises(StateCheckpointError, match="Git worktree"):
        c.state_checkpoint()


def test_required_checkpoint_refuses_an_unconfigured_origin(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-b", "state", str(tmp_path)], check=True, capture_output=True)
    c = container(
        Settings(
            _env_file=None,
            state_checkpoint_required=True,
            llm_batch_state_file=tmp_path / "state.sql",
        )
    )

    with pytest.raises(StateCheckpointError, match="origin remote"):
        c.publisher(dry_run=False)


def test_configured_checkpoint_is_shared_and_dry_run_checkpoint_cannot_push(tmp_path: Path) -> None:
    git_directory(tmp_path)
    output = tmp_path / "state.sql"
    c = container(
        Settings(
            _env_file=None,
            state_checkpoint_required=True,
            llm_batch_state_file=output,
        )
    )

    checkpoint = c.state_checkpoint()
    dry_run = c.state_checkpoint(dry_run=True)
    dry_run()

    assert isinstance(checkpoint, GitBatchCheckpoint)
    assert c.state_checkpoint() is checkpoint
    assert isinstance(dry_run, NoopStateCheckpoint)
    assert c.state_checkpoint(dry_run=True) is dry_run
    assert not output.exists()


def test_required_flag_and_legacy_state_configuration_keep_their_environment_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEXINFORM_STATE_CHECKPOINT_REQUIRED", "true")
    monkeypatch.setenv("LEXINFORM_LLM_BATCH_STATE_FILE", "state/lexinform.sql")
    monkeypatch.setenv("LEXINFORM_LLM_BATCH_STATE_BRANCH", "state")

    settings = Settings(_env_file=None)

    assert settings.state_checkpoint_required
    assert settings.llm_batch_state_file == Path("state/lexinform.sql")
    assert settings.llm_batch_state_branch == "state"


@pytest.mark.parametrize("dry_run", [False, True])
def test_shared_checkpoint_respects_dry_run_and_no_reader_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    git_directory(tmp_path)
    world = World(profile="production")
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    c = replace(
        world.container,
        settings=world.container.settings.model_copy(
            update={
                "state_checkpoint_required": True,
                "llm_batch_state_file": tmp_path / "state.sql",
            }
        ),
    )
    checkpoints: list[GitBatchCheckpoint] = []

    def record(checkpoint: GitBatchCheckpoint) -> None:
        checkpoints.append(checkpoint)

    monkeypatch.setattr(GitBatchCheckpoint, "__call__", record)

    report = c.pipeline(dry_run=dry_run).run(RunOptions(term=TERM, dry_run=dry_run, publish=False))

    assert report.ok
    assert not world.publisher.sent
    if dry_run:
        assert not checkpoints and not world.batch.submitted
    else:
        assert len(checkpoints) == 2 and len(world.batch.submitted) == 1
        assert all(checkpoint is c.state_checkpoint() for checkpoint in checkpoints)

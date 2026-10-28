import json
import logging
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lexinform.adapters.state_checkpoint import GitStateCheckpoint
from lexinform.cli import app
from lexinform.errors import StateCheckpointError
from lexinform.log_context import log_context
from lexinform.logging_setup import CorrelationFilter, JsonFormatter
from lexinform.models import LlmBatch


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def state_writer(tmp_path: Path) -> tuple[Path, Path, GitStateCheckpoint]:
    remote = tmp_path / "remote"
    local = tmp_path / "local"
    remote.mkdir()
    local.mkdir()
    git(remote, "init", "--bare")
    git(local, "init", "-b", "state")
    git(local, "remote", "add", "origin", str(remote))
    checkpoint = GitStateCheckpoint(
        lambda: "-- original\n", local / "state.sql", "state", lambda: []
    )
    checkpoint()
    return remote, local, checkpoint


def rival_commit(tmp_path: Path, remote: Path, *, branch: str = "state") -> str:
    rival = tmp_path / "rival"
    git(tmp_path, "clone", "--branch", "state", str(remote), str(rival))
    (rival / "state.sql").write_text("-- rival\n")
    git(rival, "add", "state.sql")
    git(
        rival,
        "-c",
        "user.name=Rival",
        "-c",
        "user.email=rival@example.test",
        "commit",
        "-m",
        "state: rival snapshot",
    )
    git(rival, "push", "origin", f"HEAD:refs/heads/{branch}")
    return git(rival, "rev-parse", "HEAD")


@pytest.mark.parametrize("changed_dump", [False, True])
def test_foreign_remote_tip_stops_the_writer_even_when_its_dump_is_unchanged(
    tmp_path: Path, changed_dump: bool
) -> None:
    remote, local, previous = state_writer(tmp_path)
    baseline = git(local, "rev-parse", "HEAD")
    foreign = rival_commit(tmp_path, remote)
    checkpoint = (
        GitStateCheckpoint(lambda: "-- stale database\n", local / "state.sql", "state", lambda: [])
        if changed_dump
        else previous
    )

    with pytest.raises(StateCheckpointError, match="remote baseline changed"):
        checkpoint()

    assert git(remote, "rev-parse", "state") == foreign
    assert git(remote, "show", "state:state.sql") == "-- rival"
    assert git(local, "rev-parse", "HEAD") == baseline
    assert (local / "state.sql").read_text() == "-- original\n"


def test_compare_and_swap_rejects_a_race_between_remote_check_and_push(tmp_path: Path) -> None:
    remote, local, _ = state_writer(tmp_path)
    foreign = rival_commit(tmp_path, remote, branch="rival")
    hook = local / ".git" / "hooks" / "pre-push"
    hook.write_text(
        "#!/bin/sh\n"
        f"git --git-dir={shlex.quote(str(remote))} update-ref refs/heads/state {foreign}\n"
    )
    hook.chmod(0o755)
    checkpoint = GitStateCheckpoint(
        lambda: "-- stale database\n", local / "state.sql", "state", lambda: []
    )

    with pytest.raises(StateCheckpointError, match="git push"):
        checkpoint()

    assert git(remote, "rev-parse", "state") == foreign
    assert git(remote, "show", "state:state.sql") == "-- rival"


def test_lost_push_acknowledgement_is_accepted_only_after_remote_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    remote, local, _ = state_writer(tmp_path)
    caplog.set_level(logging.INFO, logger="lexinform.adapters.state_checkpoint")
    caplog.clear()
    executable = shutil.which("git")
    assert executable is not None, "the Git-backed test has already initialized a local repository"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    wrapper = binaries / "git"
    pushes = tmp_path / "pushes"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'{shlex.quote(executable)} "$@"\n'
        "status=$?\n"
        f'if [ "$3" = "push" ]; then printf "push\\n" >> {shlex.quote(str(pushes))}; fi\n'
        'if [ "$3" = "push" ] && [ "$status" = "0" ]; then exit 1; fi\n'
        'exit "$status"\n'
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binaries}{os.pathsep}{os.environ['PATH']}")
    checkpoint = GitStateCheckpoint(
        lambda: "-- confirmed\n", local / "state.sql", "state", lambda: []
    )

    checkpoint()
    checkpoint()

    assert git(remote, "rev-parse", "state") == git(local, "rev-parse", "HEAD")
    assert git(remote, "show", "state:state.sql") == "-- confirmed"
    assert git(remote, "rev-list", "--count", "state") == "2"
    assert pushes.read_text().splitlines() == ["push", "push"]
    metrics = checkpoint_metrics(caplog)
    assert [item["outcome"] for item in metrics] == ["confirmed", "confirmed"]
    assert [item["changed"] for item in metrics] == [True, False]
    assert all(item["reconciled_push"] is True for item in metrics)
    assert all(isinstance(item["push_ms"], float) for item in metrics)


def test_rejected_snapshot_is_preserved_locally_without_changing_the_remote(tmp_path: Path) -> None:
    remote, local, _ = state_writer(tmp_path)
    baseline = git(remote, "rev-parse", "state")
    hook = local / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    checkpoint = GitStateCheckpoint(
        lambda: "-- unsaved\n", local / "state.sql", "state", lambda: []
    )

    with pytest.raises(StateCheckpointError, match="git push"):
        checkpoint()
    with pytest.raises(StateCheckpointError, match="local baseline changed"):
        checkpoint()

    assert git(remote, "rev-parse", "state") == baseline
    assert git(local, "rev-parse", "HEAD") != baseline
    assert (local / "state.sql").read_text() == "-- unsaved\n"


def test_snapshot_does_not_commit_unrelated_staged_files(tmp_path: Path) -> None:
    remote, local, _ = state_writer(tmp_path)
    (local / "unrelated.txt").write_text("not part of a database checkpoint\n")
    git(local, "add", "unrelated.txt")
    checkpoint = GitStateCheckpoint(
        lambda: "-- changed\n", local / "state.sql", "state", lambda: []
    )

    checkpoint()

    assert git(remote, "ls-tree", "--name-only", "state").splitlines() == [
        "pending-batches.json",
        "state.sql",
    ]
    assert git(local, "diff", "--cached", "--name-only") == "unrelated.txt"


def test_dump_failure_preserves_the_remote_baseline(tmp_path: Path) -> None:
    remote, local, _ = state_writer(tmp_path)
    baseline = git(remote, "rev-parse", "state")

    def fail_dump() -> str:
        raise OSError("disk unavailable")

    checkpoint = GitStateCheckpoint(fail_dump, local / "state.sql", "state", lambda: [])

    with pytest.raises(StateCheckpointError, match="snapshot could not be written"):
        checkpoint()

    assert git(remote, "rev-parse", "state") == baseline
    assert git(local, "rev-parse", "HEAD") == baseline


def test_cli_checkpoint_persists_a_restorable_snapshot_and_its_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", root.handlers.copy())
    monkeypatch.setattr(root, "level", root.level)
    remote, local, _ = state_writer(tmp_path)
    database = tmp_path / "database.db"
    monkeypatch.setenv("LEXINFORM_DB_PATH", str(database))
    runner = CliRunner()

    result = runner.invoke(app, ["db", "checkpoint", str(local / "state.sql")])

    assert result.exit_code == 0, result.output
    assert "checkpoint saved to state" in result.output
    assert "PRAGMA user_version" in git(remote, "show", "state:state.sql")
    assert git(remote, "show", "state:pending-batches.json") == '{"version":1,"batches":[]}'
    restored = tmp_path / "restored.sql"
    restored.write_text(git(remote, "show", "state:state.sql"))
    monkeypatch.setenv("LEXINFORM_DB_PATH", str(tmp_path / "restored.db"))
    result = runner.invoke(app, ["db", "restore", str(restored)])
    assert result.exit_code == 0, result.output


def checkpoint_metrics(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [
        json.loads(record.getMessage().removeprefix("state checkpoint metrics "))
        for record in caplog.records
        if record.name == "lexinform.adapters.state_checkpoint"
    ]


def test_checkpoint_metrics_preserve_exact_payloads_and_unchanged_pushes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    remote, local, _ = state_writer(tmp_path)
    pushes = tmp_path / "pushes"
    hook = local / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\nprintf 'push\\n' >> {shlex.quote(str(pushes))}\n")
    hook.chmod(0o755)
    payload = "-- źródło https://secret.test/private\n"
    checkpoint = GitStateCheckpoint(lambda: payload, local / "state.sql", "state", lambda: [])
    caplog.set_level(logging.INFO, logger="lexinform.adapters.state_checkpoint")
    caplog.clear()

    with log_context(run_id=42, command_id=8):
        checkpoint()
        checkpoint()

    records = checkpoint_metrics(caplog)
    assert len(records) == 2
    for metrics in records:
        assert metrics["outcome"] == "confirmed"
        assert metrics["phase"] == "push"
        assert metrics["reconciled_push"] is False
        assert metrics["sql_bytes"] == len(payload.encode("utf-8"))
        assert metrics["manifest_bytes"] == len('{"version":1,"batches":[]}\n')
        timings = [value for key, value in metrics.items() if key.endswith("_ms")]
        assert len(timings) == 6
        assert all(isinstance(value, float) and value >= 0 for value in timings)
    assert [metrics["changed"] for metrics in records] == [True, False]
    assert pushes.read_text().splitlines() == ["push", "push"]
    assert git(remote, "rev-list", "--count", "state") == "2"
    assert (local / "state.sql").read_text() == payload
    assert git(remote, "show", "state:state.sql") + "\n" == payload
    assert git(remote, "show", "state:pending-batches.json") == '{"version":1,"batches":[]}'
    for record in caplog.records:
        assert CorrelationFilter().filter(record)
        formatted: dict[str, object] = json.loads(JsonFormatter().format(record))
        assert formatted["run_id"] == 42
        assert formatted["command_id"] == 8
        assert "secret.test" not in str(formatted)
        assert str(tmp_path) not in str(formatted)


@pytest.mark.parametrize("failure_phase", ["preflight", "dump", "snapshot", "git_prepare", "push"])
def test_checkpoint_failure_metrics_keep_the_original_fatal_error_and_unknown_stages(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, failure_phase: str
) -> None:
    remote, local, previous = state_writer(tmp_path)
    if failure_phase == "preflight":
        rival_commit(tmp_path, remote)
    elif failure_phase in {"git_prepare", "push"}:
        hook_name = "pre-commit" if failure_phase == "git_prepare" else "pre-push"
        hook = local / ".git" / "hooks" / hook_name
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)

    def dump() -> str:
        if failure_phase == "dump":
            raise OSError("private dump error")
        return "-- changed\n"

    def pending_batches() -> list[LlmBatch]:
        if failure_phase == "snapshot":
            raise OSError("private manifest error")
        return []

    checkpoint = (
        previous
        if failure_phase == "preflight"
        else GitStateCheckpoint(dump, local / "state.sql", "state", pending_batches)
    )
    caplog.set_level(logging.INFO, logger="lexinform.adapters.state_checkpoint")
    caplog.clear()
    messages = {
        "preflight": "remote baseline changed",
        "dump": "snapshot could not be written",
        "snapshot": "snapshot could not be written",
        "git_prepare": "git -c",
        "push": "git push",
    }

    with pytest.raises(StateCheckpointError, match=messages[failure_phase]):
        checkpoint()

    records = checkpoint_metrics(caplog)
    assert len(records) == 1
    metrics = records[0]
    assert metrics["outcome"] == "failed"
    assert metrics["phase"] == failure_phase
    assert metrics["reconciled_push"] is False
    assert isinstance(metrics[f"{failure_phase}_ms"], float)
    snapshot_written = failure_phase in {"git_prepare", "push"}
    assert metrics["changed"] is (True if snapshot_written else None)
    assert metrics["sql_bytes"] == (len("-- changed\n") if snapshot_written else None)
    assert metrics["manifest_bytes"] == (27 if snapshot_written else None)
    phases = ["preflight", "dump", "snapshot", "git_prepare", "push"]
    for phase in phases[phases.index(failure_phase) + 1 :]:
        assert metrics[f"{phase}_ms"] is None
    assert "private dump error" not in caplog.text
    assert "private manifest error" not in caplog.text


@pytest.mark.parametrize("dump_fails", [False, True])
def test_logging_failure_cannot_change_checkpoint_success_or_fatal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dump_fails: bool
) -> None:
    remote, local, _ = state_writer(tmp_path)

    def fail_log(*args: object, **kwargs: object) -> None:
        raise RuntimeError("logging unavailable")

    def dump() -> str:
        if dump_fails:
            raise OSError("dump unavailable")
        return "-- confirmed\n"

    checkpoint = GitStateCheckpoint(dump, local / "state.sql", "state", lambda: [])
    monkeypatch.setattr("lexinform.adapters.state_checkpoint.logger.info", fail_log)

    if dump_fails:
        with pytest.raises(StateCheckpointError, match="snapshot could not be written") as caught:
            checkpoint()
        assert isinstance(caught.value.__cause__, OSError)
        assert git(remote, "show", "state:state.sql") == "-- original"
    else:
        checkpoint()
        assert git(remote, "show", "state:state.sql") == "-- confirmed"

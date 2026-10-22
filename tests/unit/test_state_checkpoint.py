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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote, local, _ = state_writer(tmp_path)
    executable = shutil.which("git")
    assert executable is not None, "the Git-backed test has already initialized a local repository"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    wrapper = binaries / "git"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'{shlex.quote(executable)} "$@"\n'
        "status=$?\n"
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

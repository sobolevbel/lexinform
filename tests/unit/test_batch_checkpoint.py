import subprocess
from pathlib import Path

import pytest

from lexinform.adapters.batch_checkpoint import GitBatchCheckpoint


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True, text=True
    )
    return result.stdout


def test_checkpoint_is_recoverable_from_a_fresh_checkout(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    local = tmp_path / "local"
    remote.mkdir()
    local.mkdir()
    git(remote, "init", "--bare")
    git(local, "init", "-b", "state")
    git(local, "remote", "add", "origin", str(remote))
    checkpoint = GitBatchCheckpoint(lambda: "-- submitting request\n", local / "state.sql", "state")

    checkpoint()
    checkpoint()

    assert git(remote, "show", "state:state.sql") == "-- submitting request\n"
    assert git(remote, "rev-list", "--count", "state").strip() == "1"


def test_rejected_push_is_not_reported_as_a_durable_checkpoint(tmp_path: Path) -> None:
    git(tmp_path, "init", "-b", "state")
    git(tmp_path, "remote", "add", "origin", str(tmp_path / "missing"))
    checkpoint = GitBatchCheckpoint(lambda: "-- submitting\n", tmp_path / "state.sql", "state")

    with pytest.raises(RuntimeError, match="git push"):
        checkpoint()

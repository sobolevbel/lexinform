"""Local checkpoint behavior and validation for the configured Git state writer."""

import subprocess
from pathlib import Path

from lexinform.errors import StateCheckpointError


class NoopStateCheckpoint:
    def __call__(self) -> None:
        return


def validate_checkpoint_configuration(path: Path, branch: str) -> None:
    if not path.name or path.is_dir():
        raise StateCheckpointError("state checkpoint requires a SQL file path")
    if not branch.strip():
        raise StateCheckpointError("state checkpoint requires a state branch")
    for args in (
        ["git", "check-ref-format", "--branch", branch],
        ["git", "-C", str(path.resolve().parent), "rev-parse", "--git-dir"],
        ["git", "-C", str(path.resolve().parent), "remote", "get-url", "origin"],
    ):
        try:
            result = subprocess.run(args, capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StateCheckpointError(
                "state checkpoint configuration could not be verified"
            ) from exc
        if result.returncode:
            raise StateCheckpointError(
                "state checkpoint requires a valid Git worktree, branch and origin remote"
            )

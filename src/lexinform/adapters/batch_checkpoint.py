"""Persist batch submission boundaries outside an ephemeral workflow runner."""

import subprocess
from collections.abc import Callable
from pathlib import Path

from lexinform.adapters.state_snapshot import MANIFEST_NAME, write_state_snapshot
from lexinform.models import LlmBatch


class GitBatchCheckpoint:
    def __init__(
        self,
        dump: Callable[[], str],
        path: Path,
        branch: str,
        pending_batches: Callable[[], list[LlmBatch]],
    ) -> None:
        self._dump = dump
        self._path = path.resolve()
        self._branch = branch
        self._pending_batches = pending_batches

    def __call__(self) -> None:
        self._git("check-ref-format", "--branch", self._branch)
        write_state_snapshot(self._path, self._dump(), self._pending_batches())
        name = self._path.name
        self._git("add", "--", name, MANIFEST_NAME)
        changed = self._git(
            "diff", "--cached", "--quiet", "--", name, MANIFEST_NAME, allow_diff=True
        )
        if changed:
            self._git(
                "-c",
                "user.name=github-actions[bot]",
                "-c",
                "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                "commit",
                "--only",
                "-m",
                "state: batch submission checkpoint",
                "--",
                name,
                MANIFEST_NAME,
            )
        self._git("push", "origin", f"HEAD:refs/heads/{self._branch}")

    def _git(self, *args: str, allow_diff: bool = False) -> int:
        result = subprocess.run(
            ["git", "-C", str(self._path.parent), *args],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0 and not (allow_diff and result.returncode == 1):
            raise RuntimeError(
                f"batch state checkpoint failed: git {args[0]} ({result.returncode})"
            )
        return result.returncode

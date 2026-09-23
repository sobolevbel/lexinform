"""Persist batch submission boundaries outside an ephemeral workflow runner."""

import subprocess
from collections.abc import Callable
from pathlib import Path


class GitBatchCheckpoint:
    def __init__(self, dump: Callable[[], str], path: Path, branch: str) -> None:
        self._dump = dump
        self._path = path.resolve()
        self._branch = branch

    def __call__(self) -> None:
        self._git("check-ref-format", "--branch", self._branch)
        temporary = self._path.with_suffix(".sql.tmp")
        temporary.write_text(self._dump(), encoding="utf-8")
        temporary.replace(self._path)
        name = self._path.name
        self._git("add", "--", name)
        changed = self._git("diff", "--cached", "--quiet", "--", name, allow_diff=True)
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

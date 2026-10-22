"""Persist a state snapshot only while its restored Git baseline still owns the branch."""

import subprocess
from collections.abc import Callable
from pathlib import Path

from lexinform.adapters.state_snapshot import MANIFEST_NAME, write_state_snapshot
from lexinform.errors import StateCheckpointError
from lexinform.models import LlmBatch


class GitStateCheckpoint:
    def __init__(
        self,
        dump: Callable[[], str],
        path: Path,
        branch: str,
        pending_batches: Callable[[], list[LlmBatch]],
        *,
        message: str = "state: durable checkpoint",
    ) -> None:
        self._dump = dump
        self._path = path.resolve()
        self._branch = branch
        self._pending_batches = pending_batches
        self._message = message
        self._expected: str | None = None
        self._initialized = False

    def __call__(self) -> None:
        self._git("check-ref-format", "--branch", self._branch)
        self._git("rev-parse", "--git-dir")
        head = self._head()
        if not self._initialized:
            self._expected = head
            self._initialized = True
        if head != self._expected:
            raise StateCheckpointError("state checkpoint failed: local baseline changed")
        if self._remote_head() != self._expected:
            raise StateCheckpointError("state checkpoint failed: remote baseline changed")
        try:
            write_state_snapshot(self._path, self._dump(), self._pending_batches())
        except Exception as exc:
            raise StateCheckpointError(
                "state checkpoint failed: snapshot could not be written"
            ) from exc
        names = (self._path.name, MANIFEST_NAME)
        self._git("add", "--", *names)
        changed = self._run("diff", "--cached", "--quiet", "--", *names, allowed=(0, 1))
        if changed.returncode:
            self._git(
                "-c",
                "user.name=github-actions[bot]",
                "-c",
                "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                "commit",
                "--only",
                "-m",
                self._message,
                "--",
                *names,
            )
        prepared = self._head()
        if prepared is None:
            raise StateCheckpointError("state checkpoint failed: snapshot has no commit")
        target = f"refs/heads/{self._branch}"
        try:
            self._git(
                "push",
                f"--force-with-lease={target}:{self._expected or ''}",
                "origin",
                f"{prepared}:{target}",
            )
        except StateCheckpointError:
            if self._remote_head() != prepared:
                raise
        self._expected = prepared

    def _head(self) -> str | None:
        result = self._run("rev-parse", "--verify", "--quiet", "HEAD", allowed=(0, 1))
        return result.stdout.strip() if result.returncode == 0 else None

    def _remote_head(self) -> str | None:
        target = f"refs/heads/{self._branch}"
        result = self._git("ls-remote", "--refs", "--heads", "origin", target)
        rows = [line.split() for line in result.splitlines() if line.strip()]
        if not rows:
            return None
        if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != target:
            raise StateCheckpointError("state checkpoint failed: remote baseline is ambiguous")
        return rows[0][0]

    def _git(self, *args: str) -> str:
        return self._run(*args).stdout

    def _run(self, *args: str, allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["git", "-C", str(self._path.parent), *args],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StateCheckpointError(f"state checkpoint failed: git {args[0]}") from exc
        if result.returncode not in allowed:
            raise StateCheckpointError(
                f"state checkpoint failed: git {args[0]} ({result.returncode})"
            )
        return result

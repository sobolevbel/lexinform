"""Persist a state snapshot only while its restored Git baseline still owns the branch."""

import json
import logging
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic

from lexinform.adapters.state_snapshot import MANIFEST_NAME, write_state_snapshot
from lexinform.errors import StateCheckpointError
from lexinform.log_context import log_extra
from lexinform.models import LlmBatch

logger = logging.getLogger(__name__)


@dataclass
class _CheckpointMetrics:
    started: float = field(default_factory=monotonic)
    phase: str = "preflight"
    outcome: str = "failed"
    changed: bool | None = None
    reconciled_push: bool = False
    sql_bytes: int | None = None
    manifest_bytes: int | None = None
    durations: dict[str, float | None] = field(
        default_factory=lambda: dict.fromkeys(
            ("preflight_ms", "dump_ms", "snapshot_ms", "git_prepare_ms", "push_ms")
        )
    )

    @contextmanager
    def measure(self, phase: str) -> Iterator[None]:
        self.phase = phase
        started = monotonic()
        try:
            yield
        finally:
            self.durations[f"{phase}_ms"] = round((monotonic() - started) * 1000, 3)

    def log(self) -> None:
        with suppress(Exception):
            payload = {
                "outcome": self.outcome,
                "phase": self.phase,
                "changed": self.changed,
                "reconciled_push": self.reconciled_push,
                "sql_bytes": self.sql_bytes,
                "manifest_bytes": self.manifest_bytes,
                "total_ms": round((monotonic() - self.started) * 1000, 3),
                **self.durations,
            }
            logger.info("state checkpoint metrics %s", json.dumps(payload), extra=log_extra())


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
        metrics = _CheckpointMetrics()
        try:
            self._checkpoint(metrics)
            metrics.outcome = "confirmed"
        finally:
            metrics.log()

    def _checkpoint(self, metrics: _CheckpointMetrics) -> None:
        with metrics.measure("preflight"):
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
            with metrics.measure("dump"):
                dump = self._dump()
            with metrics.measure("snapshot"):
                write_state_snapshot(self._path, dump, self._pending_batches())
                del dump
        except Exception as exc:
            raise StateCheckpointError(
                "state checkpoint failed: snapshot could not be written"
            ) from exc
        with suppress(OSError):
            metrics.sql_bytes = self._path.stat().st_size
        with suppress(OSError):
            metrics.manifest_bytes = self._path.with_name(MANIFEST_NAME).stat().st_size
        with metrics.measure("git_prepare"):
            names = (self._path.name, MANIFEST_NAME)
            self._git("add", "--", *names)
            changed = self._run("diff", "--cached", "--quiet", "--", *names, allowed=(0, 1))
            metrics.changed = bool(changed.returncode)
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
        with metrics.measure("push"):
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
                metrics.reconciled_push = True
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

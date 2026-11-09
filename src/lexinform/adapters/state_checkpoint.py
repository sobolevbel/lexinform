"""Persist a state snapshot only while its restored Git baseline still owns the branch."""

import json
import logging
import os
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

from lexinform.adapters.state_snapshot import MANIFEST_NAME, pending_manifest, write_state_snapshot
from lexinform.errors import StateCheckpointError
from lexinform.log_context import log_extra
from lexinform.models import LlmBatch

logger = logging.getLogger(__name__)

_INTENT_FILE = "lexinform-state-checkpoint"
_TOKEN_TRAILER = "Lexinform-Checkpoint: "


@dataclass(frozen=True)
class _CheckpointIntent:
    baseline: str | None
    branch: str
    snapshot: str
    token: str

    @classmethod
    def read(cls, path: Path) -> _CheckpointIntent | None:
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise StateCheckpointError("state checkpoint failed: unreadable local intent") from exc
        if not isinstance(data, list) or len(data) != 4:
            raise StateCheckpointError("state checkpoint failed: invalid local intent")
        baseline, branch, snapshot, token = data
        if (baseline is not None and not isinstance(baseline, str)) or not all(
            isinstance(value, str) for value in (branch, snapshot, token)
        ):
            raise StateCheckpointError("state checkpoint failed: invalid local intent")
        return cls(baseline, branch, snapshot, token)

    def write(self, path: Path) -> None:
        try:
            with TemporaryDirectory(dir=path.parent) as directory:
                temp = Path(directory) / "intent"
                temp.write_text(json.dumps((self.baseline, self.branch, self.snapshot, self.token)))
                os.link(temp, path)
        except OSError as exc:
            raise StateCheckpointError(
                "state checkpoint failed: could not record local intent"
            ) from exc


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
        self._owned_intent_token: str | None = None

    def __call__(self) -> None:
        metrics = _CheckpointMetrics()
        try:
            self._checkpoint(metrics)
            metrics.outcome = "confirmed"
        finally:
            metrics.log()

    def _checkpoint(self, metrics: _CheckpointMetrics) -> None:
        verified_snapshot = None
        with metrics.measure("preflight"):
            self._git("check-ref-format", "--branch", self._branch)
            intent_path = Path(
                self._git("rev-parse", "--path-format=absolute", "--git-path", _INTENT_FILE).strip()
            )
            head = self._head()
            intent = _CheckpointIntent.read(intent_path)
            if intent is not None:
                recovered = self._resume(intent, head, metrics)
                owns_intent = intent.token == self._owned_intent_token
                if not owns_intent:
                    verified_snapshot = self._verify_recovered_snapshot(recovered, metrics)
                if recovered is not None:
                    self._clear_intent(intent_path)
                    intent = None
                    self._expected = recovered
                    if not owns_intent:
                        self._initialized = True
                        metrics.changed = False
                        return
            if not self._initialized:
                self._expected = head
                self._initialized = True
            if head != self._expected:
                raise StateCheckpointError("state checkpoint failed: local baseline changed")
            if self._remote_head() != self._expected:
                raise StateCheckpointError("state checkpoint failed: remote baseline changed")
        try:
            if verified_snapshot is None:
                with metrics.measure("dump"):
                    dump = self._dump()
            else:
                dump = verified_snapshot[0]
            with metrics.measure("snapshot"):
                batches = (
                    self._pending_batches() if verified_snapshot is None else verified_snapshot[1]
                )
                write_state_snapshot(self._path, dump, batches)
            del dump, verified_snapshot
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
                if intent is None:
                    intent = _CheckpointIntent(
                        self._expected, self._branch, self._path.name, uuid4().hex
                    )
                    intent.write(intent_path)
                    self._owned_intent_token = intent.token
                self._git(
                    "-c",
                    "user.name=github-actions[bot]",
                    "-c",
                    "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                    "commit",
                    "--only",
                    "-m",
                    self._message,
                    "-m",
                    _TOKEN_TRAILER + intent.token,
                    "--",
                    *names,
                )
            prepared = self._head()
            if prepared is None:
                raise StateCheckpointError("state checkpoint failed: snapshot has no commit")
        with metrics.measure("push"):
            self._push(prepared, self._expected, metrics)
            if intent is not None:
                self._clear_intent(intent_path)
        self._expected = prepared

    def _resume(
        self, intent: _CheckpointIntent, head: str | None, metrics: _CheckpointMetrics
    ) -> str | None:
        if (intent.branch, intent.snapshot) != (self._branch, self._path.name) or (
            self._initialized and intent.baseline != self._expected
        ):
            raise StateCheckpointError(
                "state checkpoint failed: local intent belongs to another baseline"
            )
        remote = self._remote_head()
        if head == intent.baseline:
            if remote != head:
                raise StateCheckpointError("state checkpoint failed: remote baseline changed")
            return None
        if head is None:
            raise StateCheckpointError("state checkpoint failed: local baseline changed")
        parent, _, body = self._git("show", "-s", "--format=%P%n%B", head).partition("\n")
        if parent != (intent.baseline or "") or not body.rstrip().endswith(
            "\n" + _TOKEN_TRAILER + intent.token
        ):
            raise StateCheckpointError("state checkpoint failed: local baseline changed")
        paths = self._git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", head)
        changed = set(paths.rstrip("\0").split("\0"))
        if not changed <= {intent.snapshot, MANIFEST_NAME}:
            raise StateCheckpointError(
                "state checkpoint failed: local intent has unrelated changes"
            )
        if remote == intent.baseline:
            with metrics.measure("push"):
                self._push(head, intent.baseline, metrics)
        elif remote != head:
            raise StateCheckpointError("state checkpoint failed: remote baseline changed")
        metrics.reconciled_push = True
        return head

    def _verify_recovered_snapshot(
        self, commit: str | None, metrics: _CheckpointMetrics
    ) -> tuple[str, list[LlmBatch]]:
        """A writer without the original intent must prove its database matches the snapshot."""
        try:
            with metrics.measure("dump"):
                dump = self._dump()
                batches = self._pending_batches()
                manifest = pending_manifest(batches)
        except Exception as exc:
            raise StateCheckpointError(
                "state checkpoint failed: snapshot could not be read"
            ) from exc
        prefix = f"{commit or ''}:"
        stored = tuple(
            self._git("rev-parse", "--verify", f"{prefix}{name}").strip()
            for name in (self._path.name, MANIFEST_NAME)
        )
        current = tuple(
            self._run("hash-object", "--stdin", input_text=value).stdout.strip()
            for value in (dump, manifest)
        )
        if stored != current:
            raise StateCheckpointError(
                "state checkpoint failed: restore the prepared snapshot before writing again"
            )
        return dump, batches

    def _clear_intent(self, path: Path) -> None:
        try:
            path.unlink()
        except OSError as exc:
            raise StateCheckpointError(
                "state checkpoint failed: could not clear local intent"
            ) from exc
        self._owned_intent_token = None

    def _push(self, prepared: str, baseline: str | None, metrics: _CheckpointMetrics) -> None:
        target = f"refs/heads/{self._branch}"
        try:
            self._git(
                "push",
                f"--force-with-lease={target}:{baseline or ''}",
                "origin",
                f"{prepared}:{target}",
            )
        except StateCheckpointError:
            if self._remote_head() != prepared:
                raise
            metrics.reconciled_push = True

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

    def _run(
        self, *args: str, allowed: tuple[int, ...] = (0,), input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["git", "-C", str(self._path.parent), *args],
                capture_output=True,
                encoding="utf-8",
                input=input_text,
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

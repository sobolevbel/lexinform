import hashlib
import math
import os
import re
import shutil
import signal
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import psycopg
from django.db import connections

from lexinform_web.ingestion.restore import MAX_DUMP_BYTES, SnapshotError

IMPORT_LOCK = 730241902
STATE_REF = "refs/ingestion/state"


@dataclass(frozen=True)
class SnapshotRef:
    commit: str
    dump_sha256: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{40}", self.commit):
            raise ValueError("expected a full Git commit SHA")
        if not re.fullmatch(r"[0-9a-f]{64}", self.dump_sha256):
            raise ValueError("expected a dump SHA-256")


@dataclass(frozen=True)
class Acquisition:
    status: Literal["ready", "unchanged", "already_running"]
    reference: SnapshotRef | None = None
    raw: bytes | None = field(default=None, repr=False)


class HistoryError(SnapshotError):
    pass


class GitSnapshotSource:
    def __init__(self, remote: str | Path, cache: Path, *, timeout: float = 60) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Git timeout must be finite and positive")
        if isinstance(remote, str):
            parsed = urlsplit(remote)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or any(character.isspace() for character in remote)
            ):
                raise ValueError("remote must be a public HTTPS URL without credentials")
        self.remote = str(remote.resolve()) if isinstance(remote, Path) else remote
        self.protocol = "file" if isinstance(remote, Path) else "https"
        self.cache = cache.resolve()
        self.timeout = timeout
        executable = shutil.which("git")
        if executable is None:
            raise SnapshotError("Git executable is unavailable")
        self.executable = executable

    def run(
        self, *arguments: str, allow_no_match: bool = False
    ) -> subprocess.CompletedProcess[bytes]:
        command = [
            self.executable,
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "credential.helper=",
            "-c",
            "protocol.allow=never",
            "-c",
            f"protocol.{self.protocol}.allow=always",
            "-c",
            "gc.auto=0",
            "-c",
            "maintenance.auto=false",
            *arguments,
        ]
        try:
            with subprocess.Popen(
                command,
                cwd=self.cache,
                env={
                    "PATH": os.defpath,
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_NO_REPLACE_OBJECTS": "1",
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            ) as process:
                try:
                    output, _ = process.communicate(timeout=self.timeout)
                finally:
                    if process.poll() is None:
                        with suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
                        process.communicate()
                result = subprocess.CompletedProcess(command, process.returncode, output)
        except OSError, subprocess.TimeoutExpired:
            raise SnapshotError("snapshot Git operation failed or timed out") from None
        if result.returncode != 0 and not (allow_no_match and result.returncode == 1):
            raise SnapshotError("snapshot Git operation failed")
        return result

    def fetch(self, previous: SnapshotRef | None) -> Acquisition:
        self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not (self.cache / "HEAD").exists():
            if any(self.cache.iterdir()):
                raise SnapshotError("snapshot cache must be a dedicated bare repository")
            self.run("init", "--bare", "--template=", "--object-format=sha1", ".")
        if self.run("rev-parse", "--is-bare-repository").stdout.strip() != b"true":
            raise SnapshotError("snapshot cache must be a dedicated bare repository")
        if self.run("rev-parse", "--is-shallow-repository").stdout.strip() != b"false":
            raise HistoryError("shallow cache cannot establish snapshot ancestry")
        self.run(
            "fetch",
            "--quiet",
            "--no-tags",
            "--no-recurse-submodules",
            "--no-auto-maintenance",
            self.remote,
            f"+refs/heads/state:{STATE_REF}",
        )
        if self.run("rev-parse", "--is-shallow-repository").stdout.strip() != b"false":
            raise HistoryError("shallow source cannot establish snapshot ancestry")
        commit = (
            self.run("rev-parse", "--verify", f"{STATE_REF}^{{commit}}").stdout.decode().strip()
        )
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise SnapshotError("invalid fetched commit")
        if previous and previous.commit != commit:
            result = self.run(
                "merge-base", "--is-ancestor", previous.commit, commit, allow_no_match=True
            )
            if result.returncode != 0:
                raise HistoryError(
                    "state history moved backwards or diverged; audited rebaseline required"
                )
        blob = f"{commit}:lexinform.sql"
        size = int(self.run("cat-file", "-s", blob).stdout)
        if not 0 < size <= MAX_DUMP_BYTES:
            raise SnapshotError("snapshot size outside allowed bounds")
        raw = self.run("cat-file", "blob", blob).stdout
        if len(raw) != size:
            raise SnapshotError("snapshot blob size mismatch")
        reference = SnapshotRef(commit, hashlib.sha256(raw).hexdigest())
        if previous and previous.commit == commit:
            if previous != reference:
                raise HistoryError("accepted snapshot hash disagrees with its Git blob")
            return Acquisition("unchanged", reference)
        return Acquisition("ready", reference, raw)


@contextmanager
def import_lock() -> Iterator[bool]:
    parameters = connections["default"].get_connection_params()
    parameters["autocommit"] = True
    with psycopg.connect(**parameters) as session, session.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [IMPORT_LOCK])
        row = cursor.fetchone()
        acquired = bool(row and row[0])
        yield acquired
        if acquired:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [IMPORT_LOCK])


@contextmanager
def acquire_snapshot(
    source: GitSnapshotSource,
    load_previous: Callable[[], SnapshotRef | None],
) -> Iterator[Acquisition]:
    with import_lock() as acquired:
        if not acquired:
            yield Acquisition("already_running")
            return
        yield source.fetch(load_previous())

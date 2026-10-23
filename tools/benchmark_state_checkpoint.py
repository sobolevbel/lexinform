"""Measure copied-state checkpoints through an isolated local Git remote without network access."""

import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.state_checkpoint import GitStateCheckpoint
from lexinform.models import RunMode, RunReport

TOOL_VERSION = 1
MAX_ITERATIONS = 20


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(directory), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout.strip()


def object_bytes(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def fingerprint() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    paths = sorted((root / "src" / "lexinform").rglob("*.py"))
    paths += [Path(__file__).resolve(), root / "uv.lock"]
    hashes = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    }
    return {
        "tool_version": TOOL_VERSION,
        "code_sha": git(root, "rev-parse", "HEAD"),
        "working_tree_dirty": bool(git(root, "status", "--porcelain")),
        "source_sha256": hashes,
        "python_version": sys.version.split()[0],
        "sqlite_version": sqlite3.sqlite_version,
    }


def measure(script: str, iterations: int, temporary: Path) -> dict[str, Any]:
    database = temporary / "copy.db"
    remote = temporary / "remote.git"
    worktree = temporary / "state"
    remote.mkdir()
    worktree.mkdir()
    git(remote, "init", "--bare", "--template=")
    git(worktree, "init", "-b", "state", "--template=")
    git(worktree, "remote", "add", "origin", str(remote))
    repo = SqliteBillRepository(database)
    samples: list[dict[str, Any]] = []
    try:
        started = time.perf_counter()
        repo.restore(script)
        restore_seconds = time.perf_counter() - started
        schema_version = repo.schema_version
        started = time.perf_counter()
        baseline = repo.dump().encode()
        dump_seconds = time.perf_counter() - started
        checkpoint = GitStateCheckpoint(
            repo.dump, worktree / "state.sql", "state", repo.list_open_llm_batches
        )

        def sample(phase: str, iteration: int | None = None) -> None:
            started = time.perf_counter()
            checkpoint()
            elapsed = time.perf_counter() - started
            samples.append(
                {
                    "phase": phase,
                    "iteration": iteration,
                    "checkpoint_seconds": round(elapsed, 6),
                    "sql_bytes": (worktree / "state.sql").stat().st_size,
                    "commit": git(worktree, "rev-parse", "HEAD"),
                    "local_git_object_bytes": object_bytes(worktree / ".git" / "objects"),
                    "remote_git_object_bytes": object_bytes(remote / "objects"),
                }
            )

        sample("initial")
        sample("unchanged")
        for iteration in range(1, iterations + 1):
            when = datetime(2000, 1, 1, tzinfo=UTC) + timedelta(seconds=iteration)
            report = RunReport(started_at=when, since=when, mode=RunMode.DELIVERIES)
            identity = repo.start_run(report)
            sample("before_simulated_effect", iteration)
            report.finished_at = when + timedelta(milliseconds=1)
            report.notes.append("Local checkpoint benchmark; no external effect was performed")
            repo.finish_run(identity, report)
            sample("after_simulated_effect", iteration)
        final = (worktree / "state.sql").read_bytes()
        before_pack = {
            "local_bytes": object_bytes(worktree / ".git" / "objects"),
            "remote_bytes": object_bytes(remote / "objects"),
        }
        started = time.perf_counter()
        git(worktree, "gc", "--prune=now")
        git(remote, "gc", "--prune=now")
        pack_seconds = time.perf_counter() - started
        result = {
            "schema_version": schema_version,
            "restore_seconds": round(restore_seconds, 6),
            "dump_seconds": round(dump_seconds, 6),
            "initial_sql_bytes": len(baseline),
            "initial_sql_gzip_bytes": len(gzip.compress(baseline, mtime=0)),
            "final_sql_bytes": len(final),
            "final_sql_gzip_bytes": len(gzip.compress(final, mtime=0)),
            "sql_growth_bytes": len(final) - len(baseline),
            "checkpoint_count": len(samples),
            "commit_count": int(git(remote, "rev-list", "--count", "state")),
            "samples": samples,
            "git_objects_before_pack": before_pack,
            "git_objects_after_pack": {
                "local_bytes": object_bytes(worktree / ".git" / "objects"),
                "remote_bytes": object_bytes(remote / "objects"),
            },
            "pack_seconds": round(pack_seconds, 6),
        }
    finally:
        repo.close()
    result["final_sqlite_bytes"] = database.stat().st_size
    return result


def run(dump: Path, output: Path, iterations: int = 3) -> dict[str, Any]:
    source = dump.resolve(strict=True)
    target = output.resolve()
    if not source.is_file():
        raise ValueError("Input dump must be an existing local SQL file")
    if target.exists() or target.is_relative_to(source.parent):
        raise ValueError("Output must be a new file outside the input directory")
    if not 1 <= iterations <= MAX_ITERATIONS:
        raise ValueError(f"Iterations must be between 1 and {MAX_ITERATIONS}")
    data = source.read_bytes()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        GIT_ALLOW_PROTOCOL="file",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
    )
    with (
        patch.dict(os.environ, environment, clear=True),
        tempfile.TemporaryDirectory(prefix="lexinform-state-checkpoint-") as directory,
    ):
        result = {
            **fingerprint(),
            "status": "measured",
            "input": str(source),
            "input_sha256": hashlib.sha256(data).hexdigest(),
            "input_sql_bytes": len(data),
            "iterations": iterations,
            "network": False,
            "transport": "isolated local filesystem Git remote",
            "limitations": [
                "Does not measure GitHub network, authentication or runner latency",
                "Report-row mutations are synthetic and do not estimate production daily growth",
                "Packed history is measured after explicit local GC, not after every checkpoint",
                "Temporary Git repositories and copied SQLite are removed after measurement",
            ],
            **measure(data.decode("utf-8"), iterations, Path(directory)),
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()
    result = run(args.dump, args.output, args.iterations)
    print(
        f"Measured {result['checkpoint_count']} local checkpoints; "
        f"SQL growth {result['sql_growth_bytes']} bytes. Results: {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

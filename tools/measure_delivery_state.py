"""Measure bounded synthetic card edits through offline production wiring and local Git."""

import argparse
import gzip
import hashlib
import json
import math
import os
import shutil
import socket
import sqlite3
import statistics
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, NoReturn
from unittest.mock import patch

from tests.fakes import make_analysis
from tests.harness import TERM, World

from lexinform.adapters.batch_checkpoint import GitBatchCheckpoint
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import Publication, PublicationStatus, RunReport
from tools.benchmark_state_checkpoint import fingerprint, git, object_bytes

TOOL_VERSION = 1
MAX_EDITS = 50
MAX_QUIET_RUNS = 20
RETENTION_DAYS = 91
NUMBER = "3039"
TITLE = "Projekt ustawy o cudzoziemcach"
EDIT_TEXT = "Новая подтверждённая редакция {index}: не менее {amount} PLN; заявление до 30 ноября."


def summary(values: list[float]) -> dict[str, int | float]:
    if not values:
        return {"count": 0, "total_seconds": 0}
    ordered = sorted(values)
    return {
        "count": len(values),
        "total_seconds": sum(values),
        "min_seconds": ordered[0],
        "median_seconds": statistics.median(values),
        "p95_seconds": ordered[math.ceil(len(ordered) * 0.95) - 1],
        "max_seconds": ordered[-1],
    }


@dataclass
class Timings:
    checkpoints: list[float] = field(default_factory=list)
    pushes: list[float] = field(default_factory=list)


@contextmanager
def offline(timings: Timings) -> Iterator[None]:
    original_checkpoint = GitBatchCheckpoint.__call__
    original_process = subprocess.run
    environment = {
        key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TMPDIR") if key in os.environ
    }
    environment.update(
        GIT_ALLOW_PROTOCOL="file",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
    )

    def refuse(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("External sockets are forbidden in this offline benchmark")

    def checkpoint(writer: GitBatchCheckpoint) -> None:
        started = time.perf_counter()
        try:
            original_checkpoint(writer)
        finally:
            timings.checkpoints.append(time.perf_counter() - started)

    def process(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        started = time.perf_counter()
        try:
            return original_process(*args, **kwargs)
        finally:
            command = args[0] if args else kwargs.get("args")
            if (
                isinstance(command, (list, tuple))
                and command
                and command[0] == "git"
                and "push" in command
            ):
                timings.pushes.append(time.perf_counter() - started)

    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        for target, name in (
            (socket.socket, "connect"),
            (socket.socket, "connect_ex"),
            (socket, "create_connection"),
            (socket, "getaddrinfo"),
        ):
            stack.enter_context(patch.object(target, name, refuse))
        stack.enter_context(patch.object(GitBatchCheckpoint, "__call__", checkpoint))
        stack.enter_context(patch.object(subprocess, "run", process))
        yield


def source_manifest() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    fixture_paths = (
        "tests/harness.py",
        "tests/fakes.py",
        "tools/measure_delivery_state.py",
        "tools/benchmark_state_checkpoint.py",
    )
    hashes = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in fixture_paths
    }
    fixture = {
        "profile": "production",
        "reader_attention": "on",
        "number": NUMBER,
        "title": TITLE,
        "edit_text": EDIT_TEXT,
        "files": hashes,
    }
    return {
        **fingerprint(),
        "tool_version": TOOL_VERSION,
        "fixture": fixture,
        "fixture_sha256": hashlib.sha256(json.dumps(fixture, sort_keys=True).encode()).hexdigest(),
    }


def ready() -> World:
    world = World(
        profile="production", reader_attention="on", batch_script={NUMBER: make_analysis()}
    )
    world.add_bill(NUMBER, TITLE)
    world.run(max_publish=0)
    world.batch.resolve()
    world.run(max_publish=0)
    world.run()
    logical_card(world)
    assert not world.llm.contexts, (
        "the production fixture completes analysis through its fake batch"
    )
    return world


def logical_card(world: World) -> Publication:
    card = world.publication(NUMBER)
    assert card is not None and card.id is not None, (
        "the production fixture publishes one logical card"
    )
    return card


def change(world: World, index: int) -> None:
    record = world.bill(NUMBER).analysis
    assert record is not None, "the production fixture completed its analysis batch"
    analysis = record.analysis.model_copy(
        update={"practical_impact": EDIT_TEXT.format(index=index, amount=200 + index)}
    )
    world.repo.save_analysis(TERM, NUMBER, record.model_copy(update={"analysis": analysis}))


def pipeline(world: World, *, active: bool = True) -> RunReport:
    return world.run(
        discover=False,
        max_analyze=0,
        max_text_prefilter=0,
        max_publish=0,
        commands=False,
        digest=False,
        publish=active,
        track=active,
    )


def connect(world: World, directory: Path) -> Path:
    directory.mkdir()
    remote, local = directory / "remote.git", directory / "state"
    remote.mkdir()
    local.mkdir()
    git(remote, "init", "--bare", "--template=")
    git(local, "init", "-b", "state", "--template=")
    git(local, "remote", "add", "origin", str(remote))
    world.container = replace(
        world.container,
        settings=world.container.settings.model_copy(
            update={"llm_batch_state_file": local / "state.sql", "state_checkpoint_required": True}
        ),
    )
    world.container.state_checkpoint()()
    world.pipeline = world.container.pipeline(dry_run=False)
    return remote


def counts(script: str) -> dict[str, Any]:
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(script)
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        rows = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }
        revisions = dict(
            connection.execute(
                "SELECT status, COUNT(*) FROM publications "
                "WHERE kind='message_part' AND ref LIKE 'card/%/1' GROUP BY status"
            )
        )
        payloads = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(delivery_json AS BLOB))),0) "
            "FROM publications WHERE kind='message_part' AND ref LIKE 'card/%/1' "
            "AND delivery_json IS NOT NULL"
        ).fetchone()
    connection.close()
    return {
        "table_rows": rows,
        "total_rows": sum(rows.values()),
        "revision_statuses": revisions,
        "revision_rows": sum(revisions.values()),
        "revision_rows_with_payload": payloads[0],
        "revision_delivery_json_utf8_bytes": payloads[1],
    }


def history(remote: Path, compacted: Path) -> dict[str, Any]:
    identities = [
        line.split(" ", 1)[0] for line in git(remote, "rev-list", "--objects", "--all").splitlines()
    ]
    result = subprocess.run(
        ["git", "-C", str(remote), "cat-file", "--batch-check=%(objecttype) %(objectsize)"],
        input="\n".join(identities) + "\n",
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    sizes: dict[str, int] = {}
    for line in result.stdout.splitlines():
        kind, size = line.split()
        sizes[kind] = sizes.get(kind, 0) + int(size)
    shutil.copytree(remote, compacted)
    started = time.perf_counter()
    git(compacted, "gc", "--prune=now")
    elapsed = time.perf_counter() - started
    commits = int(git(remote, "rev-list", "--count", "state"))
    assert int(git(compacted, "rev-list", "--count", "state")) == commits, (
        "compaction keeps reachable history"
    )
    return {
        "commit_count": commits,
        "reachable_object_count": len(identities),
        "reachable_uncompressed_object_bytes_by_kind": sizes,
        "filesystem_file_bytes_before_gc": object_bytes(remote),
        "filesystem_file_bytes_after_gc_on_copy": object_bytes(compacted),
        "gc_copy_seconds": elapsed,
    }


def snapshot(world: World, remote: Path, directory: Path) -> dict[str, Any]:
    directory.mkdir()
    script = world.repo.dump()
    encoded = script.encode()
    started = time.perf_counter()
    git(directory, "clone", "--quiet", "--branch", "state", str(remote), "clone")
    clone_seconds = time.perf_counter() - started
    saved = (directory / "clone/state.sql").read_text()
    assert saved == script, "the remote snapshot matches the measured repository"
    database = directory / "restored.db"
    fresh = SqliteBillRepository(database)
    try:
        started = time.perf_counter()
        fresh.migrate()
        initialization_seconds = time.perf_counter() - started
        started = time.perf_counter()
        fresh.restore(saved)
        restore_seconds = time.perf_counter() - started
        restored = fresh.dump()
        assert counts(restored) == counts(script), "fresh restore preserves measured rows"
    finally:
        fresh.close()
    assert not Path(str(database) + "-wal").exists(), (
        "closing the fresh repository consolidates WAL"
    )
    return {
        "utf8_dump_bytes": len(encoded),
        "gzip9_dump_bytes_mtime0": len(gzip.compress(encoded, compresslevel=9, mtime=0)),
        "fresh_restored_sqlite_file_bytes": database.stat().st_size,
        "dump_sha256": hashlib.sha256(encoded).hexdigest(),
        "restore_dump_byte_identical": restored == script,
        "counts": counts(script),
        "git_history": history(remote, directory / "compacted.git"),
        "local_clone_seconds": clone_seconds,
        "sqlite_initialization_seconds": initialization_seconds,
        "sqlite_restore_seconds": restore_seconds,
    }


def measure(edits: int, quiet_runs: int, directory: Path, timings: Timings) -> dict[str, Any]:
    with ExitStack() as stack:
        world = ready()
        stack.callback(world.repo.close)
        baseline = world.repo.dump()
        initial_clock = world.clock.now()
        card = logical_card(world)
        before_edits, before_posts = len(world.publisher.edited), len(world.publisher.sent)
        before_batches = len(world.batch.submitted)
        remote = connect(world, directory / "main")
        milestones: list[dict[str, Any]] = []
        completed = quiet = previous_checkpoint = previous_push = 0

        def milestone(name: str) -> None:
            nonlocal previous_checkpoint, previous_push
            milestones.append(
                {
                    "name": name,
                    "completed_single_edits": completed,
                    "quiet_runs": quiet,
                    "physical_reader_edits_total": len(world.publisher.edited) - before_edits,
                    "physical_reader_posts_added": len(world.publisher.sent) - before_posts,
                    **snapshot(world, remote, directory / name),
                    "local_checkpoint_phase": summary(timings.checkpoints[previous_checkpoint:]),
                    "local_push_phase": summary(timings.pushes[previous_push:]),
                }
            )
            previous_checkpoint, previous_push = len(timings.checkpoints), len(timings.pushes)

        milestone("baseline")
        for index in range(1, edits + 1):
            world.clock.advance(minutes=1)
            change(world, index)
            report = pipeline(world)
            assert report.cards_refreshed == 1, (
                "each changed edition produces one confirmed single edit"
            )
            parts = world.repo.card_refresh_parts(card.id or 0)
            assert len(parts) == 1 and parts[0].delivery is not None, (
                "a single revision saves one operation"
            )
            assert (
                parts[0].status is PublicationStatus.SENT
                and parts[0].delivery.edit_message_id == card.message_id
            )
            world.container.state_checkpoint()()
            completed += 1
            if index in {min(10, edits), edits}:
                milestone(f"after_{index}_edits")
        for _ in range(quiet_runs):
            world.clock.advance(minutes=1)
            assert pipeline(world).cards_refreshed == 0, "quiet runs create no reader edition"
            world.container.state_checkpoint()()
            quiet += 1
        milestone("after_quiet_runs")
        assert counts(world.repo.dump())["revision_rows"] == edits, (
            "quiet runs create no revision operations"
        )
        world.clock.advance(days=RETENTION_DAYS)
        pipeline(world, active=False)
        world.container.state_checkpoint()()
        milestone("after_retention")
        retained = milestones[-1]["counts"]
        assert retained["revision_rows"] == edits and retained["revision_rows_with_payload"] == 0
        assert (
            len(world.publisher.edited) - before_edits == edits
            and len(world.publisher.sent) == before_posts
        )
        assert len(world.batch.submitted) == before_batches and not world.llm.contexts
        control = World(profile="production", reader_attention="on")
        stack.callback(control.repo.close)
        control.repo.restore(baseline)
        control.clock.current = world.clock.now()
        control_remote = connect(control, directory / "control")
        pipeline(control, active=False)
        control.container.state_checkpoint()()
        control_sample = snapshot(control, control_remote, directory / "control_sample")
        assert (
            control_sample["counts"]["revision_rows"] == 0
            and not control.publisher.sent
            and not control.publisher.edited
        )
        protected: list[dict[str, Any]] = []
        for status in (PublicationStatus.FAILED, PublicationStatus.UNKNOWN):
            fork = World(profile="production", reader_attention="on")
            stack.callback(fork.repo.close)
            fork.repo.restore(baseline)
            fork.add_bill(NUMBER, TITLE)
            fork.clock.current = initial_clock
            fork_remote = connect(fork, directory / status.value)
            change(fork, edits + 1)
            if status is PublicationStatus.FAILED:
                fork.publisher.fail_on.add(NUMBER)
            else:
                fork.publisher.unknown_on_edit.add(NUMBER)
            assert (
                fork.container.tracking_service(dry_run=False)
                .check_bill(fork.bill(NUMBER))
                .cards_refreshed
                == 0
            )
            operation = fork.repo.card_refresh_parts(card.id or 0)[0]
            assert operation.status is status and operation.delivery is not None, (
                "the unresolved operation freezes its plan"
            )
            original_plan = operation.delivery.model_dump_json()
            fork.container.state_checkpoint()()
            before = counts(fork.repo.dump())
            fork.clock.advance(days=RETENTION_DAYS)
            pipeline(fork, active=False)
            fork.container.state_checkpoint()()
            sample = snapshot(fork, fork_remote, directory / f"protected_{status.value}")
            fresh = World(profile="production")
            stack.callback(fresh.repo.close)
            fresh.repo.restore(
                (directory / f"protected_{status.value}/clone/state.sql").read_text()
            )
            saved = fresh.repo.publication_by_id(operation.id or 0)
            assert saved is not None and saved.status is status and saved.delivery is not None, (
                "a fresh clone reloads the retained unresolved operation"
            )
            assert saved.delivery.model_dump_json() == original_plan, (
                "retention and fresh clone preserve unresolved bytes"
            )
            assert logical_card(fresh).rendered_sha256 == card.rendered_sha256, (
                "unconfirmed editions do not advance the logical card"
            )
            assert not fork.batch.submitted and not fork.llm.contexts, (
                "unresolved retention forks do not request new model work"
            )
            protected.append(
                {
                    "status": status.value,
                    "attempts": saved.attempts,
                    "plan_retained_byte_identical": True,
                    "fresh_clone_status_preserved": True,
                    "before_counts": before,
                    **sample,
                }
            )
        return {
            "fixture": {
                "confirmed_edits": edits,
                "quiet_runs": quiet_runs,
                "retention_elapsed_days": RETENTION_DAYS,
            },
            "milestones": milestones,
            "no_edit_retention_control": control_sample,
            "retained_minus_control_utf8_bytes": milestones[-1]["utf8_dump_bytes"]
            - control_sample["utf8_dump_bytes"],
            "unresolved_retention": protected,
            "additional_model_calls": 0,
        }


def run(output: Path, edits: int = 10, quiet_runs: int = 3) -> dict[str, Any]:
    target = output.resolve()
    if target.exists() or output.is_symlink():
        raise ValueError("Output must be a new file")
    if not 1 <= edits <= MAX_EDITS or not 0 <= quiet_runs <= MAX_QUIET_RUNS:
        raise ValueError(f"Edits must be 1..{MAX_EDITS}; quiet runs must be 0..{MAX_QUIET_RUNS}")
    timings = Timings()
    with (
        offline(timings),
        tempfile.TemporaryDirectory(prefix="lexinform-delivery-state-") as temporary,
    ):
        manifest = source_manifest()
        measurements = measure(edits, quiet_runs, Path(temporary), timings)
        assert source_manifest() == manifest, "measured source and fixture files remain unchanged"
    result = {
        "schema": 1,
        "result": "measured",
        "source_manifest": manifest,
        "network": False,
        "transport": "isolated local filesystem Git remote",
        **measurements,
        "all_local_checkpoint_timings": summary(timings.checkpoints),
        "all_local_push_timings": summary(timings.pushes),
        "limitations": [
            "One bounded synthetic production-profile World over fake APIs, models and publisher; "
            "not production growth or a storage-backend forecast.",
            "Whole UTF-8 dump and gzip level9/mtime0 include all rows; SQLite is freshly "
            "migrated/restored/closed identically, not a long-running file's freelist allocation.",
            "Each edit uses the normal pipeline, pre/post delivery checkpoints and a final "
            "workflow-style save; quiet runs add reports and final saves only.",
            "Git sizes include metadata and all reachable history; GC runs on an isolated copy "
            "and does not expire old commits or historical payloads.",
            "Local push/checkpoint/clone timings include local-clone optimization "
            "and do not predict GitHub or production network latency.",
            "The no-edit control uses identical initial SQL and final clock; the retained "
            "difference also includes final metadata and analysis, not an exact per-row cost.",
            "Temporary databases, clones and Git remotes are removed; "
            "this dev-only tool requires tests.harness and the development dependencies.",
        ],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--edits", type=int, default=10)
    parser.add_argument("--quiet-runs", type=int, default=3)
    args = parser.parse_args()
    result = run(args.output, args.edits, args.quiet_runs)
    print(
        f"Measured {args.edits} edits and "
        f"{result['all_local_checkpoint_timings']['count']} local checkpoints: {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

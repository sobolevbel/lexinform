import hashlib
import shutil
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from lexinform.adapters.batch_checkpoint import GitBatchCheckpoint
from lexinform.adapters.state_snapshot import write_state_snapshot
from lexinform.container import Container
from lexinform.models import LlmBatch
from lexinform.ports import StateCheckpoint

MODULES = frozenset(
    {
        "test_legacy_notice_freeze.py",
        "test_legacy_digest_freeze.py",
        "test_legacy_sitting_group_freeze.py",
        "test_update_remote_checkpoints.py",
        "test_review_remote_checkpoints.py",
        "test_notice_remote_checkpoints.py",
        "test_group_remote_checkpoints.py",
        "test_digest_remote_checkpoints.py",
        "test_update_edit_remote_checkpoints.py",
        "test_long_sitting_remote_checkpoints.py",
        "test_card_edit_multipart.py",
        "test_card_revision_reader.py",
        "test_legacy_status_edit_freeze.py",
        "test_legacy_status_reply_freeze.py",
        "test_legacy_reply_multipart.py",
        "test_legacy_reply_parent.py",
    }
)


@dataclass
class SnapshotStore:
    remotes: dict[Path, Path] = field(default_factory=dict)
    writers: dict[Path, SnapshotCheckpoint] = field(default_factory=dict)

    def git(self, directory: Path, args: tuple[str, ...]) -> str:
        if args[0] == "init":
            return ""
        if args[:3] == ("remote", "add", "origin"):
            self.remotes[directory] = Path(args[3])
            return ""
        if args[0] == "show" and args[1].startswith("state:"):
            return (directory / args[1].split(":", 1)[1]).read_text().strip()
        if args[0] == "clone" and args[1:3] == ("--branch", "state"):
            remote, local = Path(args[3]), Path(args[4])
            local.mkdir()
            for name in ("state.sql", "pending-batches.json"):
                shutil.copyfile(remote / name, local / name)
            self.remotes[local] = remote
            return ""
        if args == ("rev-parse", "state"):
            return hashlib.sha256(
                b"".join(
                    (directory / name).read_bytes()
                    for name in ("state.sql", "pending-batches.json")
                )
            ).hexdigest()
        raise AssertionError(f"Unsupported snapshot transport operation: {args}")


ACTIVE: ContextVar[SnapshotStore | None] = ContextVar("checkpoint_store", default=None)


def snapshot_git(directory: Path, args: tuple[str, ...]) -> str | None:
    store = ACTIVE.get()
    return store.git(directory, args) if store is not None else None


@dataclass
class SnapshotCheckpoint(GitBatchCheckpoint):
    dump: Callable[[], str]
    path: Path
    pending: Callable[[], list[LlmBatch]]
    store: SnapshotStore

    def persist(self) -> None:
        write_state_snapshot(self.path, self.dump(), self.pending())
        payloads = {
            name: self.path.with_name(name).read_bytes()
            for name in ("state.sql", "pending-batches.json")
        }
        remote = self.store.remotes[self.path.parent]
        for name, payload in payloads.items():
            (remote / name).write_bytes(payload)


def generate_backends(metafunc: pytest.Metafunc) -> None:
    if metafunc.definition.path.name in MODULES:
        backends = (
            ["git"]
            if metafunc.config.getoption("checkpoint_transport") == "git"
            else ["snapshot", "git"]
        )
        metafunc.parametrize("checkpoint_backend", backends, indirect=True)


def select_git_anchors(items: list[pytest.Item], config: pytest.Config) -> None:
    if config.getoption("checkpoint_transport") == "git":
        return
    seen: set[str] = set()
    retained: list[pytest.Item] = []
    for item in items:
        if not isinstance(item, pytest.Function) or not hasattr(item, "callspec"):
            retained.append(item)
            continue
        if "checkpoint_backend" not in item.callspec.params:
            retained.append(item)
            continue
        if item.callspec.params["checkpoint_backend"] != "git":
            retained.append(item)
            continue
        name = item.originalname or item.name
        key = str(item.path) + "::" + name
        if (
            any(
                word in name
                for word in ("fault", "checkpoint_failure", "foreign", "unknown", "lost", "runner")
            )
            or key not in seen
        ):
            retained.append(item)
        seen.add(key)
    items[:] = retained


@pytest.fixture(autouse=True)
def checkpoint_backend(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    if getattr(request, "param", "git") == "git":
        yield
        return
    store = SnapshotStore()
    token = ACTIVE.set(store)
    original_factory = Container.state_checkpoint
    original_call = GitBatchCheckpoint.__call__

    def call(writer: GitBatchCheckpoint) -> None:
        if isinstance(writer, SnapshotCheckpoint):
            writer.persist()
        else:
            original_call(writer)

    def factory(container: Container, *, dry_run: bool = False) -> StateCheckpoint:
        path = container.settings.llm_batch_state_file
        if dry_run or path is None:
            return original_factory(container, dry_run=dry_run)
        writer = store.writers.get(path)
        if writer is None:
            writer = SnapshotCheckpoint(
                container.repo.dump, path, container.repo.list_open_llm_batches, store
            )
            store.writers[path] = writer
        return writer

    monkeypatch.setattr(Container, "state_checkpoint", factory)
    monkeypatch.setattr(GitBatchCheckpoint, "__call__", call)
    try:
        yield
    finally:
        ACTIVE.reset(token)


def add_option(parser: pytest.Parser) -> None:
    parser.addoption(
        "--checkpoint-transport",
        choices=("hybrid", "git"),
        default="hybrid",
        help="Use snapshot contract matrices with Git anchors, or replay every case through Git.",
    )

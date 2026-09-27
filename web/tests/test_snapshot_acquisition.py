import hashlib
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from lexinform_web.ingestion.acquisition import (
    GitSnapshotSource,
    HistoryError,
    SnapshotRef,
    acquire_snapshot,
    import_lock,
)
from lexinform_web.ingestion.restore import MAX_DUMP_BYTES, SnapshotError


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Snapshot test",
            "-c",
            "user.email=snapshot@example.test",
            "-C",
            str(repository),
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()


def commit(repository: Path, raw: bytes) -> str:
    (repository / "lexinform.sql").write_bytes(raw)
    git(repository, "add", "lexinform.sql")
    git(repository, "commit", "--allow-empty", "-m", "Snapshot")
    return git(repository, "rev-parse", "HEAD")


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    repository = tmp_path / "remote"
    repository.mkdir()
    git(repository, "init", "--initial-branch=state", "--object-format=sha1")
    commit(repository, b"first snapshot")
    return repository


def test_fetch_repeat_and_forward_history(remote: Path, tmp_path: Path) -> None:
    source = GitSnapshotSource(remote, tmp_path / "cache")
    first = source.fetch(None)
    assert first.status == "ready"
    assert first.raw == b"first snapshot"
    assert first.reference == SnapshotRef(
        git(remote, "rev-parse", "HEAD"), hashlib.sha256(b"first snapshot").hexdigest()
    )
    repeated = source.fetch(first.reference)
    assert repeated.status == "unchanged"
    assert repeated.raw is None
    newest = commit(remote, b"second snapshot")
    second = source.fetch(first.reference)
    assert second.reference is not None
    assert second.reference.commit == newest
    assert second.raw == b"second snapshot"
    assert second.status == "ready"


@pytest.mark.parametrize("rewrite", [False, True])
def test_reject_rollback_or_divergence(remote: Path, tmp_path: Path, rewrite: bool) -> None:
    source = GitSnapshotSource(remote, tmp_path / "cache")
    original = git(remote, "rev-parse", "HEAD")
    commit(remote, b"second snapshot")
    accepted = source.fetch(None).reference
    git(remote, "reset", "--hard", original)
    if rewrite:
        commit(remote, b"divergent snapshot")
    with pytest.raises(HistoryError, match="rebaseline"):
        source.fetch(accepted)


def test_same_commit_with_different_accepted_hash_fails(remote: Path, tmp_path: Path) -> None:
    previous = SnapshotRef(git(remote, "rev-parse", "HEAD"), "0" * 64)
    with pytest.raises(HistoryError, match="hash"):
        GitSnapshotSource(remote, tmp_path / "cache").fetch(previous)


def test_read_uses_pinned_commit_when_source_moves(remote: Path, tmp_path: Path) -> None:
    source = GitSnapshotSource(remote, tmp_path / "cache")
    original = git(remote, "rev-parse", "HEAD")
    original_run = source.run

    def move_after_pin(
        *arguments: str, allow_no_match: bool = False
    ) -> subprocess.CompletedProcess[bytes]:
        result = original_run(*arguments, allow_no_match=allow_no_match)
        if arguments[:2] == ("rev-parse", "--verify"):
            commit(remote, b"new snapshot during acquisition")
        return result

    with patch.object(source, "run", side_effect=move_after_pin):
        result = source.fetch(None)
    assert result.reference is not None
    assert result.reference.commit == original
    assert result.raw == b"first snapshot"


def test_oversized_blob_rejected_before_read(remote: Path, tmp_path: Path) -> None:
    commit(remote, b"x" * (MAX_DUMP_BYTES + 1))
    source = GitSnapshotSource(remote, tmp_path / "cache")
    with (
        patch.object(source, "run", wraps=source.run) as run,
        pytest.raises(SnapshotError, match="size"),
    ):
        source.fetch(None)
    assert not any(call.args[:2] == ("cat-file", "blob") for call in run.call_args_list)


def test_missing_dump_does_not_return_stale_cache(remote: Path, tmp_path: Path) -> None:
    source = GitSnapshotSource(remote, tmp_path / "cache")
    previous = source.fetch(None).reference
    git(remote, "rm", "lexinform.sql")
    git(remote, "commit", "-m", "Missing dump")
    with pytest.raises(SnapshotError):
        source.fetch(previous)


def test_git_timeout_terminates_helpers(remote: Path, tmp_path: Path) -> None:
    executable = tmp_path / "slow-git"
    executable.write_text("#!/bin/sh\nsleep 10\n")
    executable.chmod(0o700)
    source = GitSnapshotSource(remote, tmp_path / "cache", timeout=0.05)
    source.executable = str(executable)
    started = time.monotonic()
    with pytest.raises(SnapshotError, match="timed out"):
        source.fetch(None)
    assert time.monotonic() - started < 5


def test_reject_missing_accepted_commit(remote: Path, tmp_path: Path) -> None:
    with pytest.raises(SnapshotError):
        GitSnapshotSource(remote, tmp_path / "cache").fetch(SnapshotRef("a" * 40, "b" * 64))


@pytest.mark.django_db(transaction=True)
def test_fetch_failure_releases_lock(remote: Path, tmp_path: Path) -> None:
    source = GitSnapshotSource(remote, tmp_path / "cache")
    with (
        patch.object(source, "fetch", side_effect=SnapshotError("fetch failed")),
        pytest.raises(SnapshotError),
        acquire_snapshot(source, lambda: None),
    ):
        pytest.fail("failed fetch yielded a snapshot")
    with import_lock() as acquired:
        assert acquired


@pytest.mark.parametrize(
    "remote",
    [
        "https://secret@example.com/state",
        "ssh://example.com/state",
        "-x",
        "https://example.com/?token=secret",
    ],
)
def test_reject_credentials_and_unsupported_transports(remote: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="public HTTPS"):
        GitSnapshotSource(remote, tmp_path / "cache")


@pytest.mark.django_db(transaction=True)
def test_lock_precedes_fetch_and_previous_read(remote: Path, tmp_path: Path) -> None:
    source = GitSnapshotSource(remote, tmp_path / "cache")
    with import_lock() as first:
        assert first
        with (
            patch.object(source, "fetch", wraps=source.fetch) as fetch,
            acquire_snapshot(source, lambda: pytest.fail("previous read while locked")) as result,
        ):
            assert result.status == "already_running"
        fetch.assert_not_called()
        assert not source.cache.exists()
    with acquire_snapshot(source, lambda: None) as result:
        assert result.status == "ready"
        with import_lock() as acquired:
            assert not acquired
    with import_lock() as acquired:
        assert acquired


@pytest.mark.django_db(transaction=True)
def test_lock_released_after_caller_failure(remote: Path, tmp_path: Path) -> None:
    source = GitSnapshotSource(remote, tmp_path / "cache")
    with pytest.raises(RuntimeError), acquire_snapshot(source, lambda: None):
        raise RuntimeError("activation failed")
    with import_lock() as acquired:
        assert acquired

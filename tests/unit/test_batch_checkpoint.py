import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lexinform.adapters.batch_checkpoint import GitBatchCheckpoint
from lexinform.models import LlmBatch, PendingBatches


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True, text=True
    )
    return result.stdout


def test_checkpoint_is_recoverable_from_a_fresh_checkout(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    local = tmp_path / "local"
    remote.mkdir()
    local.mkdir()
    git(remote, "init", "--bare")
    git(local, "init", "-b", "state")
    git(local, "remote", "add", "origin", str(remote))
    checkpoint = GitBatchCheckpoint(
        lambda: "-- submitting request\n", local / "state.sql", "state", lambda: []
    )

    checkpoint()
    checkpoint()

    assert git(remote, "show", "state:state.sql") == "-- submitting request\n"
    assert git(remote, "show", "state:pending-batches.json") == '{"version":1,"batches":[]}\n'
    assert git(remote, "rev-list", "--count", "state").strip() == "1"


def test_checkpoint_updates_manifest_in_the_same_commit_as_state(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    local = tmp_path / "local"
    remote.mkdir()
    local.mkdir()
    git(remote, "init", "--bare")
    git(local, "init", "-b", "state")
    git(local, "remote", "add", "origin", str(remote))
    batches = [
        LlmBatch(
            batch_id="owned",
            provider="openai",
            call_kind="analysis",
            submitted_at=datetime(2026, 9, 24, tzinfo=UTC),
            status="submitted",
            request_count=1,
            estimated_cost_usd=0.1,
        )
    ]
    checkpoint = GitBatchCheckpoint(
        lambda: f"-- pending={len(batches)}\n", local / "state.sql", "state", lambda: batches
    )

    checkpoint()
    first = git(remote, "rev-parse", "state").strip()
    batches.clear()
    checkpoint()

    assert git(remote, "show", f"{first}:state.sql") == "-- pending=1\n"
    manifest = PendingBatches.model_validate_json(
        git(remote, "show", f"{first}:pending-batches.json")
    )
    assert [b.batch_id for b in manifest.batches] == ["owned"]
    assert git(remote, "show", "state:state.sql") == "-- pending=0\n"
    assert (
        PendingBatches.model_validate_json(
            git(remote, "show", "state:pending-batches.json")
        ).batches
        == []
    )


def test_rejected_push_is_not_reported_as_a_durable_checkpoint(tmp_path: Path) -> None:
    git(tmp_path, "init", "-b", "state")
    git(tmp_path, "remote", "add", "origin", str(tmp_path / "missing"))
    checkpoint = GitBatchCheckpoint(
        lambda: "-- submitting\n", tmp_path / "state.sql", "state", lambda: []
    )

    with pytest.raises(RuntimeError, match="git push"):
        checkpoint()

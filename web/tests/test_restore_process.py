import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

from lexinform.adapters.sqlite_repo import MIGRATIONS, SCHEMA_VERSION
from lexinform_web.ingestion.process import RestoreReceipt, restore_in_process, verify_result
from lexinform_web.ingestion.restore import SnapshotError


@pytest.fixture
def state_dump() -> bytes:
    with closing(sqlite3.connect(":memory:")) as connection:
        for migration in MIGRATIONS:
            connection.executescript(migration)
        connection.execute(
            "INSERT INTO analysis_memo (key, record_json) VALUES (?, ?)",
            ("fixture", "private data;\n'строка'"),
        )
        connection.commit()
        script = "\n".join(connection.iterdump()) + f"\nPRAGMA user_version = {SCHEMA_VERSION};"
        return script.encode()


def test_child_restore_returns_read_only_database_and_cleans_up(state_dump: bytes) -> None:
    with restore_in_process(state_dump) as snapshot:
        assert snapshot.source_schema == snapshot.normalized_schema == SCHEMA_VERSION
        assert snapshot.dump_sha256 == hashlib.sha256(state_dump).hexdigest()
        assert snapshot.connection.execute("SELECT record_json FROM analysis_memo").fetchone() == (
            "private data;\n'строка'",
        )
        path = Path(snapshot.connection.execute("PRAGMA database_list").fetchone()[2])
        assert path.exists()
        assert path.parent.stat().st_mode & 0o777 == 0o700
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            snapshot.connection.execute("DELETE FROM analysis_memo")
    assert not path.parent.exists()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        snapshot.connection.execute("SELECT 1")


def test_reader_failure_also_cleans_up(state_dump: bytes) -> None:
    with pytest.raises(RuntimeError, match="reader"), restore_in_process(state_dump) as snapshot:
        path = Path(snapshot.connection.execute("PRAGMA database_list").fetchone()[2])
        raise RuntimeError("reader failed")
    assert not path.parent.exists()


def test_child_does_not_inherit_environment_or_working_directory(
    state_dump: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEXINFORM_RESTORE_TEST_SECRET", "must-not-reach-child")
    monkeypatch.setenv("PYTHONPATH", "/nonexistent/hostile")
    with (
        patch("lexinform_web.ingestion.process.subprocess.run", wraps=subprocess.run) as run,
        restore_in_process(state_dump),
    ):
        pass
    arguments = run.call_args
    assert arguments.kwargs["env"] == {}
    assert arguments.kwargs["close_fds"] is True
    assert arguments.kwargs["cwd"] != Path.cwd()
    assert "-I" in arguments.args[0]
    assert not arguments.kwargs["cwd"].exists()


@pytest.mark.parametrize("raw", [b"secret invalid SQL", b"\xff"])
def test_child_failure_is_sanitized_and_cleans_up(raw: bytes) -> None:
    with (
        patch("lexinform_web.ingestion.process.subprocess.run", wraps=subprocess.run) as run,
        pytest.raises(SnapshotError, match="failed validation") as caught,
        restore_in_process(raw),
    ):
        pytest.fail("invalid snapshot accepted")
    assert "secret" not in str(caught.value)
    assert not run.call_args.kwargs["cwd"].exists()


def test_timeout_kills_worker_and_removes_temporary_results(state_dump: bytes) -> None:
    with (
        patch("lexinform_web.ingestion.process.subprocess.run", wraps=subprocess.run) as run,
        pytest.raises(SnapshotError, match="deadline"),
        restore_in_process(state_dump, timeout=0.000001),
    ):
        pytest.fail("worker exceeded deadline")
    assert not run.call_args.kwargs["cwd"].exists()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_reject_invalid_deadline(state_dump: bytes, timeout: float) -> None:
    with (
        pytest.raises(ValueError, match="finite and positive"),
        restore_in_process(state_dump, timeout=timeout),
    ):
        pytest.fail("invalid deadline accepted")


@pytest.mark.parametrize("failure", ["missing", "oversized", "dump", "size", "database"])
def test_reject_incomplete_or_mismatched_worker_results(tmp_path: Path, failure: str) -> None:
    raw = b"database fixture"
    receipt = RestoreReceipt(
        source_schema=1,
        normalized_schema=SCHEMA_VERSION,
        dump_sha256="a" * 64,
        database_sha256=hashlib.sha256(raw).hexdigest(),
        database_bytes=len(raw),
    )
    (tmp_path / "normalized.sqlite").write_bytes(raw)
    (tmp_path / "receipt.json").write_text(receipt.model_dump_json())
    if failure == "missing":
        (tmp_path / "receipt.json").unlink()
    elif failure == "oversized":
        (tmp_path / "receipt.json").write_text(" " * 1025 + receipt.model_dump_json())
    elif failure == "size":
        (tmp_path / "normalized.sqlite").write_bytes(raw + b"extra")
    elif failure == "database":
        (tmp_path / "normalized.sqlite").write_bytes(b"x" * len(raw))
    with pytest.raises(SnapshotError):
        verify_result(tmp_path, "b" * 64 if failure == "dump" else "a" * 64)


def test_worker_resource_limits_are_applied_in_a_real_child() -> None:
    script = (
        "import json,resource,sys; "
        "from lexinform_web.ingestion.restore_worker import limit_resources; "
        "limit_resources(); "
        "kinds=[resource.RLIMIT_CPU,resource.RLIMIT_FSIZE,"
        "resource.RLIMIT_NOFILE,resource.RLIMIT_CORE]; "
        "kinds += [resource.RLIMIT_AS] if sys.platform == 'linux' else []; "
        "print(json.dumps([resource.getrlimit(kind) for kind in kinds]))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script],
        env={},
        capture_output=True,
        timeout=5,
        check=True,
    )
    limits = json.loads(result.stdout)
    assert limits[:4] == [[10, 10], [134217728, 134217728], [32, 32], [0, 0]]
    if sys.platform == "linux":
        assert limits[4] == [536870912, 536870912]

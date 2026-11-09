import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Literal

import pytest
from tools.corpus_artifacts import (
    create_only,
    digest,
    observation_changes,
    read_artifact,
    write_json,
)


def test_interrupted_binary_write_leaves_the_final_path_available_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "original.pdf"
    original_open = Path.open

    @contextmanager
    def fail_on_close(path: Path, mode: Literal["xb"]) -> Iterator[BinaryIO]:
        with original_open(path, mode) as stream:
            yield stream
        raise OSError("interrupted write")

    with monkeypatch.context() as fault:
        fault.setattr(Path, "open", fail_on_close)
        with pytest.raises(OSError, match="interrupted write"):
            create_only(target, b"original")
    assert not list(tmp_path.iterdir())

    create_only(target, b"original")

    assert target.read_bytes() == b"original"


@pytest.mark.parametrize("winner", [b"original", b"different"])
def test_concurrent_publish_preserves_the_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winner: bytes
) -> None:
    target = tmp_path / "original.pdf"
    original_link = os.link

    def publish_first(staged: Path, destination: Path) -> None:
        assert not destination.exists()
        assert staged.read_bytes() == b"original"
        destination.write_bytes(winner)
        original_link(staged, destination)

    monkeypatch.setattr(os, "link", publish_first)

    if winner == b"original":
        create_only(target, b"original")
    else:
        with pytest.raises(ValueError, match="Refusing to replace"):
            create_only(target, b"original")

    assert target.read_bytes() == winner
    assert list(tmp_path.iterdir()) == [target]


def test_failed_json_serialization_does_not_leave_an_installed_record(tmp_path: Path) -> None:
    target = tmp_path / "record.json"

    with pytest.raises(TypeError, match="not JSON serializable"):
        write_json(target, {"status": "installed", "unsupported": object()})
    assert not list(tmp_path.iterdir())

    write_json(target, {"status": "installed"})

    assert json.loads(target.read_bytes()) == {"status": "installed"}


def test_report_writes_are_exclusive_and_preserve_public_input_bytes(tmp_path: Path) -> None:
    source = tmp_path / "input.json"
    source.write_bytes(b'{"value":1}')
    target = tmp_path / "report.json"

    write_json(target, {"source": digest(read_artifact(source))})

    assert json.loads(target.read_bytes()) == {
        "source": hashlib.sha256(source.read_bytes()).hexdigest()
    }
    with pytest.raises(FileExistsError):
        write_json(target, {"replacement": True})
    assert source.read_bytes() == b'{"value":1}'


def test_credential_and_symlink_artifacts_are_rejected(tmp_path: Path) -> None:
    private = tmp_path / ".env"
    private.write_text("private")
    alias = tmp_path / "alias.json"
    alias.symlink_to(private)

    for path in (private, alias):
        with pytest.raises(ValueError, match="credential file"):
            read_artifact(path)
    with pytest.raises(ValueError, match="symlink"):
        write_json(alias, {})
    assert private.read_text() == "private"


def test_shared_runtime_and_rcl_helpers_do_not_load_test_suites() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import tools.corpus_runtime, tools.corpus_artifacts, tools.corpus_rcl; assert not any(n == 'pytest' or n.startswith('tests.') for n in sys.modules)",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("previous", [True, False])
def test_missing_observation_is_distinct_from_an_explicit_unknown(previous: bool) -> None:
    unknown: dict[str, object] = {"input": None}
    missing: dict[str, object] = {}

    changed = (
        observation_changes(unknown, missing) if previous else observation_changes(missing, unknown)
    )

    assert changed == ["input"]


def test_observation_comparison_keeps_equal_unknowns_and_sorts_changes() -> None:
    previous: dict[str, object] = {"unknown": None, "z": {"sha256": "old"}}
    current: dict[str, object] = {"unknown": None, "a": {"sha256": "new"}}

    assert observation_changes(previous, current) == ["a", "z"]

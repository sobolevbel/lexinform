import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from tools.corpus_artifacts import digest, observation_changes, read_artifact, write_json


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

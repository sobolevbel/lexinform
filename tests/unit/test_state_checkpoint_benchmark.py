import hashlib
import json
from pathlib import Path

import pytest
from tools import benchmark_state_checkpoint

from lexinform.adapters.sqlite_repo import SqliteBillRepository


def input_dump(tmp_path: Path) -> Path:
    directory = tmp_path / "inputs"
    directory.mkdir()
    dump = directory / "state.sql"
    repo = SqliteBillRepository(":memory:")
    try:
        repo.migrate()
        dump.write_text(repo.dump())
    finally:
        repo.close()
    return dump


def test_benchmark_preserves_input_and_measures_both_changed_and_unchanged_snapshots(
    tmp_path: Path,
) -> None:
    dump = input_dump(tmp_path)
    original = dump.read_bytes()
    output = tmp_path / "result.json"

    result = benchmark_state_checkpoint.run(dump, output, iterations=1)

    assert dump.read_bytes() == original
    assert result["input_sha256"] == hashlib.sha256(original).hexdigest()
    assert result["network"] is False
    assert result["checkpoint_count"] == 4
    assert result["commit_count"] == 3
    assert result["sql_growth_bytes"] > 0
    assert result["final_sqlite_bytes"] > 0
    samples = result["samples"]
    assert [sample["phase"] for sample in samples] == [
        "initial",
        "unchanged",
        "before_simulated_effect",
        "after_simulated_effect",
    ]
    assert samples[0]["commit"] == samples[1]["commit"]
    assert result == json.loads(output.read_text())
    assert sorted(path.name for path in dump.parent.iterdir()) == ["state.sql"]


def test_benchmark_refuses_overwrite_and_outputs_inside_the_input_directory(tmp_path: Path) -> None:
    dump = input_dump(tmp_path)
    output = tmp_path / "result.json"
    output.write_text("prior evidence")

    with pytest.raises(ValueError, match="new file outside"):
        benchmark_state_checkpoint.run(dump, output)
    with pytest.raises(ValueError, match="new file outside"):
        benchmark_state_checkpoint.run(dump, dump.parent / "result.json")

    assert output.read_text() == "prior evidence"


@pytest.mark.parametrize("iterations", [0, -1, 21])
def test_benchmark_rejects_unbounded_iteration_requests(tmp_path: Path, iterations: int) -> None:
    dump = input_dump(tmp_path)
    output = tmp_path / "result.json"

    with pytest.raises(ValueError, match="Iterations must be between"):
        benchmark_state_checkpoint.run(dump, output, iterations)

    assert not output.exists()

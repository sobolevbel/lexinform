import json
import os
import socket
from pathlib import Path

import pytest
from tools import measure_delivery_state


@pytest.mark.parametrize("edits", [1, 2])
def test_delivery_measurement_counts_whole_state_and_preserves_unresolved_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edits: int
) -> None:
    monkeypatch.setenv("LEXINFORM_STATE_CHECKPOINT_REQUIRED", "true")
    monkeypatch.setenv("LEXINFORM_LLM_BATCH_STATE_FILE", str(tmp_path / "must-not-exist.sql"))
    output = tmp_path / "measurement.json"
    original_connect = socket.socket.connect

    result = measure_delivery_state.run(output, edits=edits, quiet_runs=2)

    assert result == json.loads(output.read_text())
    assert socket.socket.connect is original_connect
    assert os.environ["LEXINFORM_STATE_CHECKPOINT_REQUIRED"] == "true"
    assert os.environ["LEXINFORM_LLM_BATCH_STATE_FILE"] == str(tmp_path / "must-not-exist.sql")
    assert result["network"] is False and result["additional_model_calls"] == 0
    manifest = result["source_manifest"]
    assert len(manifest["code_sha"]) == 40 and len(manifest["fixture_sha256"]) == 64
    assert "tests/harness.py" in manifest["fixture"]["files"]
    baseline, confirmed, quiet, retained = result["milestones"]
    assert baseline["counts"]["revision_rows"] == 0
    assert confirmed["physical_reader_edits_total"] == edits
    assert quiet["physical_reader_edits_total"] == edits
    assert quiet["counts"]["revision_rows"] == confirmed["counts"]["revision_rows"] == edits
    assert quiet["counts"]["table_rows"]["runs"] == confirmed["counts"]["table_rows"]["runs"] + 2
    assert quiet["local_checkpoint_phase"]["count"] == 2
    assert retained["counts"]["revision_rows"] == edits
    assert retained["counts"]["revision_rows_with_payload"] == 0
    assert retained["git_history"]["commit_count"] > quiet["git_history"]["commit_count"]
    assert (
        retained["git_history"]["reachable_uncompressed_object_bytes_by_kind"]["blob"]
        > retained["utf8_dump_bytes"]
    )
    for milestone in result["milestones"]:
        assert milestone["utf8_dump_bytes"] > milestone["gzip9_dump_bytes_mtime0"] > 0
        assert milestone["fresh_restored_sqlite_file_bytes"] > 0
        assert milestone["physical_reader_posts_added"] == 0
        assert milestone["restore_dump_byte_identical"]
        assert milestone["local_clone_seconds"] > 0 and milestone["sqlite_restore_seconds"] > 0
    control = result["no_edit_retention_control"]
    assert control["counts"]["revision_rows"] == 0
    assert retained["utf8_dump_bytes"] > control["utf8_dump_bytes"]
    assert {item["status"] for item in result["unresolved_retention"]} == {"failed", "unknown"}
    for item in result["unresolved_retention"]:
        assert item["plan_retained_byte_identical"] and item["fresh_clone_status_preserved"]
        assert item["counts"]["revision_rows_with_payload"] == 1
        assert item["counts"]["revision_statuses"] == {item["status"]: 1}
    assert (
        result["all_local_push_timings"]["count"] == result["all_local_checkpoint_timings"]["count"]
    )
    assert sorted(path.name for path in tmp_path.iterdir()) == ["measurement.json"]


def test_delivery_measurement_refuses_existing_output_before_measuring(tmp_path: Path) -> None:
    output = tmp_path / "measurement.json"
    output.write_text("prior evidence")

    with pytest.raises(ValueError, match="new file"):
        measure_delivery_state.run(output, edits=1, quiet_runs=0)

    assert output.read_text() == "prior evidence"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["measurement.json"]


@pytest.mark.parametrize("edits,quiet_runs", [(0, 1), (51, 1), (1, -1), (1, 21)])
def test_delivery_measurement_refuses_unbounded_work(
    tmp_path: Path, edits: int, quiet_runs: int
) -> None:
    output = tmp_path / "measurement.json"

    with pytest.raises(ValueError, match="Edits must"):
        measure_delivery_state.run(output, edits=edits, quiet_runs=quiet_runs)

    assert not output.exists()

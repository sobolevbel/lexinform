import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("command", ["collect-batches", "drain-deliveries"])
@pytest.mark.parametrize("boundary", [1, 2])
def test_cli_reader_checkpoint_failure_exits_75_before_any_later_effect(
    tmp_path: Path, command: str, boundary: int
) -> None:
    output = tmp_path / "evidence.json"
    root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.cli_delivery_checkpoint",
            command,
            str(boundary),
            str(output),
        ],
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join((str(root / "src"), str(root))),
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "LEXINFORM_TELEGRAM_BOT_TOKEN": "",
            "LEXINFORM_TELEGRAM_CHANNEL_ID": "",
            "LEXINFORM_TELEGRAM_LOG_CHANNEL_ID": "",
        },
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 75, result.stderr
    assert "injected CLI reader checkpoint failure" in result.stderr
    evidence = json.loads(output.read_text())
    assert evidence["boundary"] == [boundary]
    assert evidence["effects"] == ([evidence["target"]] if boundary == 2 else [])
    assert evidence["reader_posts"] == boundary - 1 and evidence["reader_edits"] == 0
    assert evidence["remote_status"] == ("pending" if boundary == 2 else "queued")
    assert evidence["remote_has_html"] is (boundary == 2)

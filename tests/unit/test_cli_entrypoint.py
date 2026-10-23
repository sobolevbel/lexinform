import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Never

import pytest

from lexinform import cli
from lexinform.errors import StateCheckpointError
from lexinform.settings import Settings


def test_checkpoint_failure_has_a_distinct_process_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_checkpoint() -> None:
        raise StateCheckpointError("state checkpoint failed: remote baseline changed")

    monkeypatch.setattr(cli, "app", fail_checkpoint)

    with pytest.raises(SystemExit) as failure:
        cli.main()

    assert failure.value.code == 75
    assert "remote baseline changed" in capsys.readouterr().err


def test_keyboard_interrupt_retains_its_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "app", interrupted)

    with pytest.raises(SystemExit) as failure:
        cli.main()

    assert failure.value.code == 130


def test_startup_does_not_convert_checkpoint_configuration_failure_to_an_ordinary_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_configuration(settings: Settings) -> Never:
        raise StateCheckpointError("missing production checkpoint")

    monkeypatch.setattr(cli, "build_container", reject_configuration)

    with pytest.raises(StateCheckpointError, match="missing production checkpoint"):
        cli.run()


@pytest.mark.parametrize("entrypoint", ["module", "console"])
def test_real_entrypoints_preserve_the_fatal_checkpoint_exit(
    tmp_path: Path, entrypoint: str
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    subprocess.run(["git", "init", "-b", "state", str(state)], check=True, capture_output=True)
    executable = shutil.which("lexinform")
    assert executable is not None, "uv installs the project's console entrypoint for the gate"
    command = [sys.executable, "-m", "lexinform"] if entrypoint == "module" else [executable]

    result = subprocess.run(
        [*command, "db", "checkpoint", str(state / "state.sql")],
        env={
            **os.environ,
            "LEXINFORM_DB_PATH": str(tmp_path / "isolated.db"),
            "LEXINFORM_TELEGRAM_BOT_TOKEN": "",
            "LEXINFORM_TELEGRAM_CHANNEL_ID": "",
            "LEXINFORM_TELEGRAM_LOG_CHANNEL_ID": "",
        },
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 75, result.stderr
    assert "state checkpoint requires a valid Git worktree" in result.stderr

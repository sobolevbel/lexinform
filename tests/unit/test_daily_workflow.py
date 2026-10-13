import os
import re
import subprocess
from pathlib import Path

import pytest


def execution_script() -> str:
    workflow = Path(__file__).parents[2] / ".github/workflows/daily.yml"
    lines = workflow.read_text().splitlines()
    start = lines.index("      - name: Run lexinform")
    start = lines.index("        run: |", start) + 1
    script: list[str] = []
    for line in lines[start:]:
        if line and not line.startswith("          "):
            break
        script.append(line[10:])
    return "\n".join(script)


def test_only_enabled_jobs_share_a_non_replacing_state_queue() -> None:
    workflow = Path(__file__).parents[2] / ".github/workflows/daily.yml"
    text = workflow.read_text()
    assert re.search(r"^concurrency:", text, re.MULTILINE) is None
    lock = re.search(r"^    concurrency:\n((?:      .+\n)+)", text, re.MULTILINE)
    assert lock is not None
    options = dict(line.strip().split(": ", 1) for line in lock.group(1).splitlines())
    assert options == {"group": "lexinform-daily", "cancel-in-progress": "false", "queue": "max"}


@pytest.mark.parametrize("pending_commands", [False, True])
@pytest.mark.parametrize("drain_exit", [0, 3])
def test_hourly_delivery_tick_preserves_inbox_and_delivery_failures(
    tmp_path: Path, pending_commands: bool, drain_exit: int
) -> None:
    executable = tmp_path / "uv"
    executable.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*"\n'
        'if [ "$5" = "drain-deliveries" ]; then exit "$DRAIN_EXIT"; fi\n'
    )
    executable.chmod(0o755)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    if pending_commands:
        (inbox / "1.json").write_text("{}")
    environment = {
        "PATH": f"{tmp_path}{os.pathsep}{os.defpath}",
        "INBOX_PRESENT": "true",
        "LEXINFORM_INBOX_DIR": str(inbox),
        "EVENT_NAME": "schedule",
        "EVENT_TYPE": "",
        "EVENT_SCHEDULE": "43 * * * *",
        "DRAIN_EXIT": str(drain_exit),
    }

    result = subprocess.run(
        ["bash", "-c", execution_script()],
        env=environment,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == drain_exit, result.stderr
    commands = ["run --frozen --no-dev lexinform drain-deliveries"]
    if pending_commands:
        commands.append("run --frozen --no-dev lexinform commands")
    assert result.stdout.splitlines() == commands


def test_manual_delivery_dry_run_keeps_the_workflow_from_executing_inbox(tmp_path: Path) -> None:
    executable = tmp_path / "uv"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$*"\n')
    executable.chmod(0o755)
    environment = {
        "PATH": f"{tmp_path}{os.pathsep}{os.defpath}",
        "INBOX_PRESENT": "true",
        "EVENT_NAME": "workflow_dispatch",
        "EVENT_TYPE": "",
        "INPUT_COMMAND": "drain-deliveries",
        "INPUT_OPTIONS": "",
        "INPUT_SINCE": "",
        "INPUT_DRY_RUN": "true",
    }

    result = subprocess.run(
        ["bash", "-c", execution_script()],
        env=environment,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "run --frozen --no-dev lexinform drain-deliveries --dry-run"
    ]

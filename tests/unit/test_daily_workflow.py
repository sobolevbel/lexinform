import os
import re
import subprocess
from pathlib import Path

import pytest


def execution_script(step: str = "Run lexinform") -> str:
    workflow = Path(__file__).parents[2] / ".github/workflows/daily.yml"
    lines = workflow.read_text().splitlines()
    start = lines.index(f"      - name: {step}")
    start = lines.index("        run: |", start) + 1
    script: list[str] = []
    for line in lines[start:]:
        if line and not line.startswith("          "):
            break
        script.append(line[10:])
    return "\n".join(script)


@pytest.mark.parametrize("run", ["failure", "success"])
@pytest.mark.parametrize(
    ("dump", "expected"),
    [
        ("success", "state saved successfully"),
        ("failure", "state save failed; the state may be unsaved"),
        ("skipped", "state save skipped; the state may be unsaved"),
        ("", "state save outcome unavailable; the state may be unsaved"),
    ],
)
def test_failure_alert_reports_the_actual_state_save_outcome(
    tmp_path: Path, run: str, dump: str, expected: str
) -> None:
    executable = tmp_path / "curl"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    executable.chmod(0o755)
    environment = {
        "PATH": f"{tmp_path}{os.pathsep}{os.defpath}",
        "LEXINFORM_TELEGRAM_BOT_TOKEN": "fake-token",
        "LEXINFORM_TELEGRAM_LOG_CHANNEL_ID": "test-channel",
        "LEXINFORM_RUN_MESSAGE_ID": "123",
        "RUN_URL": "https://example.test/run/243",
        "RUN": run,
        "RESTORE": "success",
        "DUMP": dump,
    }

    result = subprocess.run(
        ["bash", "-c", execution_script("Tell the log channel when the job failed around the run")],
        env=environment,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    messages = [line for line in result.stdout.splitlines() if line.startswith("text=")]
    assert messages == [
        "text=❌ lexinform: daily job failed "
        f"(run: {run}, restore: success, dump/push: {dump}); "
        f"{expected}: https://example.test/run/243"
    ]
    assert ("the state may be unsaved" in messages[0]) == (dump != "success")
    assert "message_id=123" in result.stdout.splitlines()


def test_only_enabled_jobs_share_a_non_replacing_state_queue() -> None:
    workflow = Path(__file__).parents[2] / ".github/workflows/daily.yml"
    text = workflow.read_text()
    assert re.search(r"^concurrency:", text, re.MULTILINE) is None
    lock = re.search(r"^    concurrency:\n((?:      .+\n)+)", text, re.MULTILINE)
    assert lock is not None
    options = dict(line.strip().split(": ", 1) for line in lock.group(1).splitlines())
    assert options == {"group": "lexinform-daily", "cancel-in-progress": "false", "queue": "max"}


def test_final_state_save_uses_the_shared_checkpoint_writer(tmp_path: Path) -> None:
    executable = tmp_path / "uv"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$*"\n')
    executable.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", execution_script("Dump database and push state branch")],
        env={
            "PATH": f"{tmp_path}{os.pathsep}{os.defpath}",
            "STATE_FILE": "state/lexinform.sql",
            "STATE_BRANCH": "state",
        },
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "run --frozen --no-dev lexinform db checkpoint state/lexinform.sql --branch state"
    ]


def test_inbox_deletions_require_a_confirmed_state_save() -> None:
    workflow = Path(__file__).parents[2] / ".github/workflows/daily.yml"
    text = workflow.read_text()
    step = text.split("      - name: Commit handled commands and push inbox branch\n", 1)[1]
    condition = step.splitlines()[0]

    assert "steps.dump.outcome == 'success'" in condition
    assert "inputs.dry_run != true" in condition


@pytest.mark.parametrize("pending_commands", [False, True])
@pytest.mark.parametrize("drain_exit", [0, 3, 75])
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
    if pending_commands and drain_exit != 75:
        commands.append("run --frozen --no-dev lexinform commands")
    assert result.stdout.splitlines() == commands


@pytest.mark.parametrize("drain_exit", [3, 75])
def test_manual_fatal_checkpoint_stops_the_following_commands_process(
    tmp_path: Path, drain_exit: int
) -> None:
    executable = tmp_path / "uv"
    executable.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*"\n'
        'if [ "$5" = "drain-deliveries" ]; then exit "$DRAIN_EXIT"; fi\n'
    )
    executable.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", execution_script()],
        env={
            "PATH": f"{tmp_path}{os.pathsep}{os.defpath}",
            "INBOX_PRESENT": "true",
            "EVENT_NAME": "workflow_dispatch",
            "EVENT_TYPE": "",
            "INPUT_COMMAND": "drain-deliveries",
            "INPUT_OPTIONS": "",
            "INPUT_SINCE": "",
            "INPUT_DRY_RUN": "false",
            "DRAIN_EXIT": str(drain_exit),
        },
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == drain_exit, result.stderr
    commands = ["run --frozen --no-dev lexinform drain-deliveries"]
    if drain_exit != 75:
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

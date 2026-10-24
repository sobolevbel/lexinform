import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.state_checkpoint import GitStateCheckpoint
from lexinform.models import BillStatus, ProcessDetail
from tests.scenario.test_card_remote_checkpoints import git
from tests.unit.test_daily_workflow import execution_script


@pytest.mark.parametrize("backfill_exit", [1, 75, 124])
def test_final_workflow_checkpoint_restores_partial_backfill_after_the_run_step_fails(
    tmp_path: Path, process_3039: ProcessDetail, now: datetime, backfill_exit: int
) -> None:
    database = tmp_path / "lexinform.db"
    repository = SqliteBillRepository(database)
    repository.migrate()
    repository.upsert_summary(process_3039, now=now)
    repository.reset_bill(10, "3039", BillStatus.SKIPPED_PREFILTER)
    remote, state = tmp_path / "remote.git", tmp_path / "state"
    remote.mkdir()
    state.mkdir()
    git(remote, "init", "--bare", "--template=")
    git(state, "init", "-b", "state", "--template=")
    git(state, "remote", "add", "origin", str(remote))
    GitStateCheckpoint(
        repository.dump, state / "lexinform.sql", "state", repository.list_open_llm_batches
    )()
    baseline = git(remote, "rev-parse", "state")
    repository.close()
    executable = tmp_path / "uv"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\nfrom pathlib import Path\nfrom unittest.mock import patch\n"
        "from lexinform import cli\n"
        "from lexinform.adapters.sqlite_repo import SqliteBillRepository\n"
        "from lexinform.models import BillStatus\n"
        "print(json.dumps(sys.argv[1:]), flush=True)\n"
        "if sys.argv[5] == 'reprefilter':\n"
        "    repo = SqliteBillRepository(Path(os.environ['LEXINFORM_DB_PATH']))\n"
        "    repo.reset_bill(10, '3039', BillStatus.ANALYSIS_PENDING)\n"
        "    repo.close()\n"
        "    sys.exit(int(os.environ['BACKFILL_EXIT']))\n"
        "assert sys.argv[5:7] == ['db', 'checkpoint'], 'no later reader process is permitted'\n"
        "sys.argv = ['lexinform', *sys.argv[5:]]\n"
        "with patch('socket.socket.connect', side_effect=AssertionError('No network')), "
        "patch('socket.create_connection', side_effect=AssertionError('No network')):\n"
        "    cli.main()\n"
    )
    executable.chmod(0o755)
    root = Path(__file__).resolve().parents[2]
    environment = {
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "PYTHONPATH": os.pathsep.join((str(root / "src"), str(root))),
        "GIT_ALLOW_PROTOCOL": "file",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "LEXINFORM_DB_PATH": str(database),
        "LEXINFORM_TELEGRAM_BOT_TOKEN": "",
        "LEXINFORM_TELEGRAM_CHANNEL_ID": "",
        "LEXINFORM_TELEGRAM_LOG_CHANNEL_ID": "",
        "INBOX_PRESENT": "true",
        "EVENT_NAME": "workflow_dispatch",
        "EVENT_TYPE": "",
        "INPUT_COMMAND": "run",
        "INPUT_OPTIONS": "",
        "INPUT_SINCE": "",
        "INPUT_DRY_RUN": "false",
        "INPUT_INDEX_RCL_SINCE": "2026-10-01",
        "INPUT_REPREFILTER_LIMIT": "5",
        "INPUT_REPREFILTER_TEXT_SKIPPED": "false",
        "BACKFILL_EXIT": str(backfill_exit),
        "STATE_FILE": str(state / "lexinform.sql"),
        "STATE_BRANCH": "state",
    }

    run = subprocess.run(
        ["bash", "-c", execution_script()],
        env=environment,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert run.returncode == backfill_exit, run.stderr
    assert [json.loads(line) for line in run.stdout.splitlines()] == [
        ["run", "--frozen", "--no-dev", "lexinform", "reprefilter", "--limit", "5"]
    ]
    assert git(remote, "rev-parse", "state") == baseline

    checkpoint = subprocess.run(
        ["bash", "-c", execution_script("Dump database and push state branch")],
        env=environment,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert checkpoint.returncode == 0, checkpoint.stderr
    assert git(remote, "rev-parse", "state") != baseline
    database.unlink()
    clone = tmp_path / "fresh-clone"
    git(tmp_path, "clone", "--branch", "state", str(remote), str(clone))
    fresh = SqliteBillRepository(tmp_path / "fresh.db")
    try:
        fresh.restore((clone / "lexinform.sql").read_text())
        bill = fresh.get(10, "3039")
        assert bill is not None, "the remote snapshot preserves the seeded bill"
        assert bill.status is BillStatus.ANALYSIS_PENDING
        assert json.loads((clone / "pending-batches.json").read_text())["batches"] == []
    finally:
        fresh.close()

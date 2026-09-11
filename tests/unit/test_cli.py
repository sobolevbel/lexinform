"""Operator commands through typer, against a temporary database (no network, no Telegram)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.cli import app
from lexinform.models import (
    AnalysisRecord,
    BillStatus,
    ProcessDetail,
    RunMode,
    RunReport,
    TokenUsage,
)
from tests.fakes import make_analysis

runner = CliRunner()


@pytest.fixture
def db(tmp_path: Path, process_3039: ProcessDetail) -> Path:
    """A database with druk 3039 analysed as not relevant after two failed attempts."""
    path = tmp_path / "t.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    now = datetime(2026, 9, 7, tzinfo=UTC)
    repo.upsert_summary(process_3039, now=now)
    repo.record_analysis_failure(10, "3039", "boom")
    repo.record_analysis_failure(10, "3039", "boom")
    repo.save_analysis(
        10,
        "3039",
        AnalysisRecord(
            analysis=make_analysis(relevant=False, score=1),
            model="m",
            prompt_version="v",
            input_chars=10,
            truncated=False,
            text_source="pdf",
            created_at=now,
        ),
    )
    repo.close()
    return path


def _env(db: Path, term: str = "10") -> dict[str, str]:
    # The term is pinned so that no command asks the live API which term is running. No bot
    # token and no log channel, whatever the developer's `.env` says (see `conftest.py`):
    # replies and reports go to the console, never to Telegram.
    return {
        "LEXINFORM_DB_PATH": str(db),
        "LEXINFORM_TELEGRAM_CHANNEL_ID": "@test",
        "LEXINFORM_TELEGRAM_BOT_TOKEN": "",
        "LEXINFORM_TELEGRAM_LOG_CHANNEL_ID": "",
        "LEXINFORM_TERM": term,
    }


def _status(db: Path) -> tuple[BillStatus, int, str | None]:
    repo = SqliteBillRepository(db)
    bill = repo.get(10, "3039")
    repo.close()
    assert bill is not None
    return bill.status, bill.analysis_attempts, bill.last_error


def _record_run(db: Path, started: datetime, *, analyzed: int, input_tokens: int) -> None:
    repo = SqliteBillRepository(db)
    report = RunReport(
        started_at=started,
        since=started,
        mode=RunMode.RUN,
        analyzed=analyzed,
        published=1,
        llm_input_tokens=input_tokens,
        llm_output_tokens=500,
        llm_usage={"claude-opus-5": TokenUsage(input=input_tokens, output=500)},
    )
    run_id = repo.start_run(report)
    report.finished_at = started + timedelta(minutes=3)
    repo.finish_run(run_id, report)
    repo.close()


def test_runs_lists_the_recorded_runs_newest_first(db: Path) -> None:
    _record_run(db, datetime(2026, 9, 8, 4, 23, tzinfo=UTC), analyzed=2, input_tokens=100_000)
    _record_run(db, datetime(2026, 9, 9, 4, 23, tzinfo=UTC), analyzed=1, input_tokens=20_000)

    result = runner.invoke(app, ["runs", "--days", "3650"], env=_env(db))

    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.startswith("2026-")]
    assert lines[0].startswith("2026-09-09 04:23") and "$0.11" in lines[0]
    assert lines[1].startswith("2026-09-08 04:23") and "$0.51" in lines[1]


def test_cost_sums_the_runs_and_names_the_dearest_analyses(db: Path) -> None:
    _record_run(db, datetime(2026, 9, 8, 4, 23, tzinfo=UTC), analyzed=2, input_tokens=100_000)
    _record_run(db, datetime(2026, 9, 9, 4, 23, tzinfo=UTC), analyzed=1, input_tokens=20_000)

    result = runner.invoke(app, ["cost", "--days", "3650"], env=_env(db))

    assert result.exit_code == 0, result.output
    assert "2 run(s) in the last 3650 days: $0.62 total, $0.31 per run" in result.output
    assert "claude-opus-5: in 120.0k" in result.output
    assert "most expensive run: 2026-09-08 04:23 ($0.51, 2 analysed)" in result.output
    assert "3039" not in result.output  # the fixture's analysis has no token counts to rank


def test_reset_puts_the_bill_back_with_a_clean_budget(db: Path) -> None:
    result = runner.invoke(app, ["reset", "3039", "--to", "analysis_pending", "-y"], env=_env(db))

    assert result.exit_code == 0, result.output
    assert "analyzed (attempts 2) -> analysis_pending" in result.output
    assert _status(db) == (BillStatus.ANALYSIS_PENDING, 0, None)


def test_a_bill_of_an_older_term_is_still_found_after_the_sejm_moved_on(db: Path) -> None:
    # The Sejm is in term 11 now; druk 3039 of term 10 is still in the database.
    result = runner.invoke(
        app, ["reset", "3039", "--to", "analysis_pending", "-y"], env=_env(db, term="11")
    )

    assert result.exit_code == 0, result.output
    assert _status(db)[0] is BillStatus.ANALYSIS_PENDING


def test_reset_asks_before_changing_anything(db: Path) -> None:
    result = runner.invoke(app, ["reset", "3039"], env=_env(db), input="n\n")

    assert result.exit_code == 1
    assert _status(db)[0] is BillStatus.ANALYZED


def test_republish_refuses_a_bill_without_a_relevant_analysis(db: Path) -> None:
    result = runner.invoke(app, ["republish", "3039", "-y"], env=_env(db))

    assert result.exit_code == 2
    assert "no relevant analysis" in result.output


def test_commands_answers_the_inbox_and_empties_it(db: Path, tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "5.json").write_text(
        json.dumps(
            {
                "update_id": 5,
                "chat_id": "-1001",
                "message_id": 9,
                "text": "/show 3039",
                "received_at": "2026-09-11T08:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (inbox / "README.md").write_text("not a command", encoding="utf-8")

    # The container builds the Telegram publisher eagerly: a dummy token, and an API address
    # nothing listens on, so that an accidental send fails here instead of reaching Telegram.
    env = {
        **_env(db),
        "LEXINFORM_INBOX_DIR": str(inbox),
        "LEXINFORM_TELEGRAM_BOT_TOKEN": "test-token",
        "LEXINFORM_TELEGRAM_API_BASE_URL": "http://127.0.0.1:9",
    }
    result = runner.invoke(app, ["commands"], env=env)

    assert result.exit_code == 0, result.output
    assert "/show 3039 → 3039 shown" in result.output
    assert "handled=1 failed=0 errors=0" in result.output
    assert sorted(p.name for p in inbox.iterdir()) == ["README.md"]


def test_commands_without_an_inbox_says_what_to_set(db: Path) -> None:
    result = runner.invoke(app, ["commands"], env=_env(db))

    assert result.exit_code == 2
    assert "LEXINFORM_INBOX_DIR" in result.output


def test_db_dump_and_restore_round_trip(db: Path, tmp_path: Path) -> None:
    dump = tmp_path / "state.sql"

    dumped = runner.invoke(app, ["db", "dump", str(dump)], env=_env(db))
    fresh = tmp_path / "fresh.db"
    restored = runner.invoke(app, ["db", "restore", str(dump)], env=_env(fresh))

    assert dumped.exit_code == 0 and dump.exists()
    assert restored.exit_code == 0, restored.output
    assert _status(fresh) == (BillStatus.ANALYZED, 2, None)  # save_analysis clears the error


def test_db_restore_of_a_missing_dump_is_an_error_unless_allowed(tmp_path: Path) -> None:
    missing = tmp_path / "absent.sql"
    env = _env(tmp_path / "t.db")

    strict = runner.invoke(app, ["db", "restore", str(missing)], env=env)
    lenient = runner.invoke(app, ["db", "restore", str(missing), "--missing-ok"], env=env)

    assert strict.exit_code == 2
    assert lenient.exit_code == 0 and "empty database" in lenient.output

"""Operator commands through typer, against a temporary database (no network, no Telegram)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.cli import app
from lexinform.models import AnalysisRecord, BillStatus, ProcessDetail
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
    # The term is pinned so that no command asks the live API which term is running.
    return {
        "LEXINFORM_DB_PATH": str(db),
        "LEXINFORM_TELEGRAM_CHANNEL_ID": "@test",
        "LEXINFORM_TERM": term,
    }


def _status(db: Path) -> tuple[BillStatus, int, str | None]:
    repo = SqliteBillRepository(db)
    bill = repo.get(10, "3039")
    repo.close()
    assert bill is not None
    return bill.status, bill.analysis_attempts, bill.last_error


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

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


def _env(db: Path) -> dict[str, str]:
    return {"LEXINFORM_DB_PATH": str(db), "LEXINFORM_TELEGRAM_CHANNEL_ID": "@test"}


def test_reset_puts_the_bill_back_with_a_clean_budget(db: Path) -> None:
    result = runner.invoke(app, ["reset", "3039", "--to", "analysis_pending", "-y"], env=_env(db))
    assert result.exit_code == 0, result.output
    assert "analyzed (attempts 2) -> analysis_pending" in result.output
    repo = SqliteBillRepository(db)
    bill = repo.get(10, "3039")
    repo.close()
    assert bill is not None and bill.status is BillStatus.ANALYSIS_PENDING
    assert bill.analysis_attempts == 0 and bill.last_error is None


def test_reset_asks_before_changing_anything(db: Path) -> None:
    result = runner.invoke(app, ["reset", "3039"], env=_env(db), input="n\n")
    assert result.exit_code == 1
    repo = SqliteBillRepository(db)
    assert repo.get(10, "3039").status is BillStatus.ANALYZED  # type: ignore[union-attr]
    repo.close()


def test_republish_refuses_a_bill_without_a_relevant_analysis(db: Path) -> None:
    result = runner.invoke(app, ["republish", "3039", "-y"], env=_env(db))
    assert result.exit_code == 2
    assert "no relevant analysis" in result.output

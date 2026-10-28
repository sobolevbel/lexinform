import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import ProcessDetail
from lexinform.settings import Settings
from tests.database import empty_repository
from tests.settings import ScenarioSettings
from tests.unit.test_applicability import captured


def test_scenario_settings_ignore_environment_and_dotenv_but_validate_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEXINFORM_MIN_SCORE", "1")
    dotenv = tmp_path / ".env"
    dotenv.write_text("LEXINFORM_MIN_SCORE=2\n")

    assert (
        ScenarioSettings(_env_file=dotenv).min_score == Settings.model_fields["min_score"].default
    )
    assert ScenarioSettings(min_score=4).min_score == 4
    with pytest.raises(ValidationError):
        ScenarioSettings(sejm_concurrency=0)


def test_cached_schema_matches_real_migrations_and_keeps_data_and_foreign_keys_isolated(
    process_3039: ProcessDetail, now: datetime
) -> None:
    with (
        closing(SqliteBillRepository(":memory:")) as migrated,
        closing(empty_repository()) as first,
        closing(empty_repository()) as second,
    ):
        migrated.migrate()
        assert first.dump() == second.dump() == migrated.dump()
        first.upsert_summary(process_3039, now=now)
        assert first.get(10, "3039") is not None
        assert second.get(10, "3039") is None
        with pytest.raises(sqlite3.IntegrityError):
            second.save_applicability_observation(captured())

from contextlib import closing
from pathlib import Path

import pytest
from tools.replay_card_history import snapshot

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import AnalysisRecord, BillStatus
from tests.fakes import make_analysis
from tests.harness import summary
from tests.unit.test_input_receipts import NOW


def state() -> str:
    with closing(SqliteBillRepository(Path(":memory:"))) as repo:
        repo.migrate()
        repo.upsert_summary(summary("RCL/1", "Controlled fixture"), now=NOW)
        repo.save_analysis(
            10,
            "RCL/1",
            AnalysisRecord(
                analysis=make_analysis(),
                model="fixture",
                prompt_version="fixture",
                input_chars=0,
                truncated=False,
                text_source="metadata_only",
                created_at=NOW,
            ),
        )
        repo.set_status(10, "RCL/1", BillStatus.ANALYZED)
        return repo.dump()


@pytest.mark.parametrize("mode", ["confirmed", "uncertain", "unbound", "multipart", "rehome"])
def test_public_card_fixture_has_frozen_operations_and_no_models(mode: str) -> None:
    value = snapshot(state(), 10, "RCL/1", mode)
    assert value["core"]["published"] and value["core"]["repeat_dump_exact"]
    assert value["core"]["restore_exact"] and value["core"]["new_model_requests"] == 0
    assert value["metadata"]["content_versions"] == 0
    statuses = [row["status"] for row in value["core"]["physical"]]
    assert "unknown" in statuses if mode == "uncertain" else "sent" in statuses

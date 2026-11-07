import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from tools.memo_retention_census import census

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import BillStatus
from tests.harness import summary
from tests.unit.test_state_retention import NOW, OLD


def test_census_matches_cleanup_eligibility_and_reports_ownerless_refs(tmp_path: Path) -> None:
    path = tmp_path / "memo.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    repo.upsert_summary(summary("3039", "Projekt ustawy"), now=OLD)
    repo.set_status(10, "3039", BillStatus.ANALYZED)
    repo.upsert_summary(summary("3040", "Projekt ustawy"), now=OLD)
    repo.set_status(10, "3040", BillStatus.ANALYZED)
    repo.set_awaiting_batch(10, "3040", OLD)
    repo.save_analysis_memo("expire", "{}", term=10, number="3039", used_at=OLD)
    repo.save_analysis_memo("active", "{}", term=10, number="3040", used_at=OLD)
    repo.save_analysis_memo("missing-bill", "{}", term=10, number="9999", used_at=OLD)
    orphan_record = '{"text":"ż"}'
    repo.save_analysis_memo("ownerless", orphan_record)
    repo.close()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "INSERT INTO llm_batch_intents VALUES (?, ?, ?, ?, ?, ?)",
            (
                "separate-owner-intent",
                "openai",
                '{"term":10,"number":"3040"}',
                '{"memo_key":"ownerless"}',
                "queued",
                OLD.isoformat(),
            ),
        )
    before = path.read_bytes()
    cutoff = NOW - timedelta(days=180)

    report = census(path, cutoff)

    assert path.read_bytes() == before
    assert report["ownerless_rows"] == 2
    assert report["ownerless_record_utf8_bytes"] == len(orphan_record.encode()) + 2
    assert report["ownerless_exclusions"] == {
        "owner_unrecorded": 1,
        "last_used_at_missing": 1,
        "bill_missing": 1,
    }
    assert report["ownerless_recorded_refs"] == {
        "intent_key_ref": 1,
        "batch_item_key_ref": 0,
        "paid_id_ref": 0,
    }
    assert report["exclusions"] == {
        "owner_unrecorded": 1,
        "last_used_at_missing": 1,
        "bill_missing": 1,
        "bill_work": 1,
        "intent": 1,
    }
    repo = SqliteBillRepository(path)
    removed = repo.prune_history(before=cutoff, memo_before=cutoff)["analysis_memos"]
    assert removed == report["eligible_rows"] == 1
    repo.close()

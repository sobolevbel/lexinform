import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.request_doctor import audit

from lexinform.adapters.input_receipts import capture_frozen_request_manifest
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import BatchIntent, BillStatus, DigestItemMeta, LlmBatch, LlmBatchItem
from lexinform.models.batch import DocumentReviewQuestion, batch_item_meta
from lexinform.models.review_prepared_input import (
    ReviewPreparedDocument,
    ReviewPreparedInputWitness,
    review_context_sha256,
)
from tests.harness import summary
from tests.unit.test_input_receipts import NOW
from tests.unit.test_review_prepared_input_replay import review_request


def review_audit(path: Path, dump: str, kind: str) -> int:
    with closing(sqlite3.connect(path)) as checkpoint:
        checkpoint.executescript(dump)
    return int(audit(path)["summary"][f"consultation_review_witness_{kind}"])


def test_review_witness_survives_consumption_cleanup_and_restore(tmp_path: Path) -> None:
    path = tmp_path / "review.db"
    frozen = review_request("openai")
    question = frozen.question
    assert isinstance(question, DocumentReviewQuestion), "fixture freezes a consultation review"
    context = question.ctx
    intent = BatchIntent.from_request(
        request=frozen,
        meta=DigestItemMeta(memo_key=context.memo_key(frozen.term), review_context=context),
        provider="openai",
        created_at=NOW,
    )
    batch = LlmBatch(
        batch_id="review-fixture",
        provider="openai",
        call_kind="consultation_review",
        submitted_at=NOW,
        status="ended",
        request_count=1,
        estimated_cost_usd=0.01,
    )
    item = LlmBatchItem.from_intent(batch.batch_id, intent)
    manifest = capture_frozen_request_manifest(frozen, "openai")
    assert manifest is not None, "fixture emits the native frozen provider envelope"
    witness = ReviewPreparedInputWitness(
        term=frozen.term,
        number=frozen.number,
        request_sha256=manifest.request_sha256,
        manifest_sha256=manifest.manifest_sha256,
        context_sha256=review_context_sha256(context),
        documents=tuple(ReviewPreparedDocument.from_document(doc) for doc in context.documents),
    )
    assert isinstance(item.meta, DigestItemMeta), "fixture retains secondary metadata"
    legacy = item.meta.model_dump_json()
    assert "review_input_witness" not in legacy
    assert batch_item_meta(item.call_kind, legacy) == item.meta

    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.upsert_summary(summary(item.number, "Projekt ustawy"), now=NOW)
        repo.set_status(item.term, item.number, BillStatus.ANALYZED)
        repo.save_llm_batch(batch, [item])
        baseline = repo.dump()
        repo.restore(baseline)
        assert repo.dump() == baseline
        assert "review_input_witness" not in baseline
        assert review_audit(tmp_path / "legacy.db", baseline, "unknown") == 1
        future = json.loads(legacy)
        future["frozen_request_manifest"] = manifest.model_dump(mode="json")
        future["review_input_witness"] = witness.model_dump(mode="json")
        serialized = json.dumps(future, ensure_ascii=False)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE llm_batch_items SET meta_json=?", (serialized,))

        (before_cleanup,) = repo.list_llm_batch_items(batch.batch_id)
        assert isinstance(before_cleanup.meta, DigestItemMeta), (
            "reader preserves secondary metadata"
        )
        assert before_cleanup.meta.review_input_witness == witness
        future_dump = repo.dump()
        assert review_audit(tmp_path / "future.db", future_dump, "known") == 1
        with closing(sqlite3.connect(tmp_path / "future.db")) as checkpoint, checkpoint:
            checkpoint.execute("UPDATE llm_batch_items SET number='9999'")
        report = audit(tmp_path / "future.db")
        assert report["status"] == "failed"
        assert report["summary"]["consultation_review_witness_known"] == 0
        assert [finding["code"] for finding in report["findings"]] == [
            "invalid_consultation_review_witness"
        ]
        repo.restore(future_dump)
        assert repo.dump() == future_dump

        for corruption, replacement in (
            ("request_sha256", "a" * 64),
            ("context_sha256", "a" * 64),
            ("manifest_sha256", "a" * 64),
            ("number", "9999"),
        ):
            modified = json.loads(serialized)
            modified["review_input_witness"][corruption] = replacement
            with pytest.raises(ValueError):
                batch_item_meta(item.call_kind, json.dumps(modified))
        modified = json.loads(serialized)
        modified["review_input_witness"]["documents"].reverse()
        with pytest.raises(ValueError, match="saved context"):
            batch_item_meta(item.call_kind, json.dumps(modified))
        with pytest.raises(ValueError, match="submitted consultation reviews"):
            batch_item_meta(item.call_kind, serialized, submitted=False)
        with pytest.raises(ValueError, match="submitted consultation reviews"):
            batch_item_meta("commission_review", serialized)
        with pytest.raises(ValueError, match="submitted item"):
            LlmBatchItem.model_validate({**before_cleanup.model_dump(), "number": "9999"})
        no_context = {**future, "review_context": None}
        with pytest.raises(ValueError, match="submitted item"):
            LlmBatchItem.model_validate({**before_cleanup.model_dump(), "meta": no_context})

        repo.mark_llm_batch_item_consumed(batch.batch_id, item.custom_id, consumed_at=NOW)
        repo.mark_batch_item_accounted(batch.batch_id, item.custom_id, at=NOW)
        repo.mark_llm_batch_forgotten(batch.batch_id, forgotten_at=NOW)
        assert repo.prune_history(before=NOW, memo_before=NOW)["batch_payloads"] == 1
        cleaned = repo.dump()
        assert review_audit(tmp_path / "cleaned.db", cleaned, "known") == 1
        assert all(doc.text not in cleaned for doc in context.documents)
        for _ in range(2):
            repo.restore(cleaned)
            assert repo.dump() == cleaned
            with closing(sqlite3.connect(path)) as db:
                (raw_meta,) = db.execute("SELECT meta_json FROM llm_batch_items").fetchone()
            restored = batch_item_meta(item.call_kind, raw_meta)
            assert isinstance(restored, DigestItemMeta), "reader restores secondary metadata"
            assert restored.review_input_witness == witness
            assert restored.review_context is None

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.replay_review_input import Provider

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import BatchIntent, BillStatus, DigestItemMeta, LlmBatch, LlmBatchItem
from lexinform.models.batch import DocumentReviewQuestion, batch_item_meta
from tests.harness import summary
from tests.unit.test_input_receipts import NOW
from tests.unit.test_review_prepared_input_replay import review_request


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("control", ["exact", "payload", "metadata"])
def test_review_witness_only_at_verified_submitting_transition(
    tmp_path: Path, provider: Provider, control: str
) -> None:
    path = tmp_path / "review.db"
    request = review_request(provider)
    question = request.question
    assert isinstance(question, DocumentReviewQuestion), "fixture freezes a consultation review"
    context = question.ctx
    if control == "payload":
        raw = request.payload_json
        assert raw is not None, "fixture has a frozen provider payload"
        changed = raw.replace("comparison/retained", "comparison/replaced")
        assert changed != raw
        request = request.model_copy(
            update={
                "payload_json": changed,
                "payload_sha256": hashlib.sha256(changed.encode()).hexdigest(),
            }
        )
    intent = BatchIntent.from_request(
        request=request,
        meta=DigestItemMeta(memo_key=context.memo_key(request.term), review_context=context),
        provider=provider,
        created_at=NOW,
    )
    batch = LlmBatch(
        batch_id="review-submit",
        provider=provider,
        call_kind="consultation_review",
        submitted_at=NOW,
        status="ended",
        request_count=1,
        estimated_cost_usd=0.01,
    )
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.upsert_summary(summary(request.number, context.title), now=NOW)
        repo.set_status(request.term, request.number, BillStatus.ANALYZED)
        repo.save_batch_intent(intent)
        queued = repo.dump()
        assert "review_input_witness" not in queued
        repo.restore(queued)
        repo.mark_batch_intents_submitting([request.custom_id])
        (submitted_intent,) = repo.list_submitting_batch_intents()
        assert submitted_intent.request == request
        item = LlmBatchItem.from_intent(batch.batch_id, submitted_intent)
        assert isinstance(item.meta, DigestItemMeta), "fixture submits secondary metadata"
        if control == "metadata":
            other = context.model_copy(update={"documents": context.documents[::-1]})
            item = item.model_copy(
                update={"meta": item.meta.model_copy(update={"review_context": other})}
            )
        with repo.atomic():
            repo.save_llm_batch(batch, [item])
            repo.delete_batch_intents([request.custom_id])
        (stored,) = repo.list_llm_batch_items(batch.batch_id)
        assert isinstance(stored.meta, DigestItemMeta), "submitted item retains secondary metadata"
        manifest = stored.meta.frozen_request_manifest
        assert manifest is not None and manifest.request_sha256 == request.payload_sha256
        assert repo.list_submitting_batch_intents() == []
        witness = stored.meta.review_input_witness
        if control != "exact":
            assert witness is None
            return
        assert witness is not None
        assert [doc.role for doc in witness.documents] == ["response", "comparison_text"]
        assert [doc.chars for doc in witness.documents] == [
            len(doc.text) for doc in context.documents
        ]
        assert all(doc.text not in witness.model_dump_json() for doc in context.documents)
        assert witness.manifest_sha256 == manifest.manifest_sha256
        repo.mark_llm_batch_item_consumed(batch.batch_id, item.custom_id, consumed_at=NOW)
        repo.mark_batch_item_accounted(batch.batch_id, item.custom_id, at=NOW)
        repo.mark_llm_batch_forgotten(batch.batch_id, forgotten_at=NOW)
        assert repo.prune_history(before=NOW, memo_before=NOW)["batch_payloads"] == 1
        cleaned = repo.dump()
        for _ in range(2):
            repo.restore(cleaned)
            assert repo.dump() == cleaned
        with closing(sqlite3.connect(path)) as db:
            (raw_meta,) = db.execute("SELECT meta_json FROM llm_batch_items").fetchone()
        retained = batch_item_meta(batch.call_kind, raw_meta)
        assert isinstance(retained, DigestItemMeta), "cleanup preserves secondary metadata"
        assert retained.review_input_witness == witness and retained.review_context is None
        assert "review_context" not in json.loads(raw_meta)

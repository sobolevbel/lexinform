import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import AmendmentsQuestion, BatchIntent, DigestItemMeta, LlmBatch, LlmBatchItem
from tests.unit.test_input_receipts import NOW
from tests.unit.test_scan_input import URL
from tests.unit.test_scan_payload_reader import committee_scan_request, offline_openai_backend


@pytest.mark.parametrize("admission", ["matching", "queued", "different_source"])
def test_submit_witness_requires_the_exact_submitting_queue(
    repo: SqliteBillRepository, admission: str
) -> None:
    request = committee_scan_request(offline_openai_backend)
    question = request.question
    assert isinstance(question, AmendmentsQuestion), "the test freezes a committee amendment"
    scan = question.ctx.scan
    assert scan is not None, "the test supplies the exact input PDF"
    intent = BatchIntent.from_request(
        request=request,
        provider="openai",
        created_at=NOW,
        meta=DigestItemMeta(
            memo_key="key",
            source_kind="committee_amendments",
            source_url=URL,
            scan_input=scan.scan_input,
        ),
    )
    repo.save_batch_intent(intent)
    assert repo.list_queued_batch_intents() == [intent]
    if admission != "queued":
        repo.mark_batch_intents_submitting([request.custom_id])
    item = LlmBatchItem.from_intent("batch", intent)
    if admission == "different_source":
        item = item.model_copy(
            update={"meta": item.meta.model_copy(update={"source_url": URL + "?other"})}
        )

    repo.save_llm_batch(
        LlmBatch(
            batch_id=item.batch_id,
            provider="openai",
            call_kind=item.call_kind,
            submitted_at=NOW,
            status="submitted",
            request_count=1,
            estimated_cost_usd=request.estimated_cost_usd,
        ),
        [item],
    )

    (saved,) = repo.list_llm_batch_items(item.batch_id)
    assert isinstance(saved.meta, DigestItemMeta), "submitted amendments have digest metadata"
    witness = saved.meta.amendment_scan_input
    if admission != "matching":
        assert witness is None
    else:
        assert witness is not None, "the exact submitting queue supplies its original witness"
        assert witness.restore_context(scan) == question.ctx
        assert scan.data not in saved.meta.model_dump_json()
    repo.delete_batch_intents([request.custom_id])
    dump = repo.dump()
    repo.restore(dump)
    assert repo.dump() == dump

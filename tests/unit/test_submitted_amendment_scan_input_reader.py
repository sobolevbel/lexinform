import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import DigestItemMeta, LlmBatch, LlmBatchItem
from lexinform.models.batch import batch_item_meta
from tests.computations import NOW
from tests.unit.test_amendment_scan_input_reader import original, paid_result


def test_submitted_scan_witness_survives_metadata_rewrite_and_restore(
    repo: SqliteBillRepository,
) -> None:
    witness, _, _ = original()
    result = paid_result(witness)
    meta = DigestItemMeta(
        memo_key="key",
        source_url=witness.source_url,
        source_kind="committee_amendments",
        request_sha256=witness.request_sha256,
        scan_input=witness.scan_input,
        input_binding=result.input_binding,
        amendment_scan_input=witness,
    )
    item = LlmBatchItem(
        batch_id=result.batch_id,
        custom_id=result.custom_id,
        call_kind="amendments",
        term=result.term,
        number=result.number,
        meta=meta,
    )
    batch = LlmBatch(
        batch_id=result.batch_id,
        provider=result.provider,
        call_kind="amendments",
        submitted_at=NOW,
        request_count=1,
        status="submitted",
        estimated_cost_usd=0,
    )

    repo.save_llm_batch(batch, [item])
    repo.save_computation_result(result)
    saved = repo.dump()
    repo.restore(saved)
    repo.restore(saved)
    (restored,) = repo.list_llm_batch_items(batch.batch_id)

    assert restored == item
    assert batch_item_meta("amendments", restored.meta.model_dump_json()) == meta
    assert repo.get_computation_result(result.result_id) == result
    assert repo.dump() == saved
    with pytest.raises(ValueError, match="invalid_amendment_scan_input"):
        batch_item_meta("amendments", meta.model_dump_json(), submitted=False)
    with pytest.raises(ValueError, match="invalid_amendment_scan_input"):
        batch_item_meta("supplement", meta.model_dump_json())
    invalid = meta.model_copy(update={"source_url": witness.source_url + "?other"})
    forged = saved.replace(meta.model_dump_json(), invalid.model_dump_json())
    assert forged != saved
    with pytest.raises(ValueError):
        repo.restore(forged)
    assert repo.dump() == saved

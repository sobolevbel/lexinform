import hashlib
from contextlib import closing
from typing import Literal

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import BatchIntent, DigestItemMeta, LlmBatch, LlmBatchItem, SupplementQuestion
from tests.unit.test_input_receipts import NOW
from tests.unit.test_scan_payload_reader import (
    offline_anthropic_backend,
    offline_openai_backend,
    supplement_scan_request,
)

Provider = Literal["openai", "anthropic"]


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("control", ["exact", "wrong_metadata", "wrong_prompt", "queued_only"])
def test_witness_captures_only_exact_submitted_supplement(provider: Provider, control: str) -> None:
    backend = offline_openai_backend if provider == "openai" else offline_anthropic_backend
    request = supplement_scan_request(backend, "impact_assessment")
    question = request.question
    assert isinstance(question, SupplementQuestion), "fixture freezes a supplement question"
    scan = question.ctx.scan
    assert scan is not None, "fixture has the original PDF attachment"
    if control == "wrong_prompt":
        raw = request.payload_json
        assert raw is not None, "fixture has a frozen provider envelope"
        changed = raw.replace("=== AKTUALNY OPIS PROJEKTU ===", "Changed prompt")
        assert changed != raw
        request = request.model_copy(
            update={
                "payload_json": changed,
                "payload_sha256": hashlib.sha256(changed.encode()).hexdigest(),
            }
        )
    intent = BatchIntent.from_request(
        request=request,
        meta=DigestItemMeta(
            memo_key=request.custom_id,
            source_kind=question.ctx.source_kind,
            source_url="https://api.sejm.gov.pl/sejm/term10/prints/3039-A/3039-A.pdf",
            title=question.ctx.document_title,
            document_number="3039-A",
            scan_input=scan.scan_input,
        ),
        provider=provider,
        created_at=NOW,
    )
    batch = LlmBatch(
        batch_id="supplement-writer",
        provider=provider,
        call_kind="supplement",
        submitted_at=NOW,
        status="submitted",
        request_count=1,
        estimated_cost_usd=0.01,
    )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_batch_intent(intent)
        assert "supplement_scan_input" not in repo.dump()
        if control != "queued_only":
            repo.mark_batch_intents_submitting([request.custom_id])
            (original,) = repo.list_submitting_batch_intents()
            assert original.request == request
        item = LlmBatchItem.from_intent(batch.batch_id, intent)
        if control == "wrong_metadata":
            meta = item.meta.model_copy(update={"document_number": "3039-B"})
            item = item.model_copy(update={"meta": meta})
        with repo.atomic():
            repo.save_llm_batch(batch, [item])
            repo.delete_batch_intents([request.custom_id])
        (saved,) = repo.list_llm_batch_items(batch.batch_id)
        assert isinstance(saved.meta, DigestItemMeta), "reader retains secondary metadata"
        witness = saved.meta.supplement_scan_input
        if control == "exact":
            assert witness is not None
            assert witness.restore_context(scan) == question.ctx
            assert scan.data not in witness.model_dump_json()
        else:
            assert witness is None
        manifest = saved.meta.frozen_request_manifest
        if control == "queued_only":
            assert manifest is None
        else:
            assert manifest is not None and manifest.request_sha256 == request.payload_sha256

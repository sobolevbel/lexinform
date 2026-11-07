import hashlib

import pytest

from lexinform.adapters.input_receipts import (
    capture_frozen_request_manifest,
    validate_provider_scan_attachment,
)
from lexinform.json_storage import canonical_json
from lexinform.models import BatchIntent, DigestItemMeta, LlmBatchItem, SupplementQuestion
from lexinform.models.batch import batch_item_meta
from lexinform.models.supplement_inputs import SupplementScanInputWitness
from tests.unit.test_input_receipts import NOW
from tests.unit.test_scan_payload_reader import offline_openai_backend, supplement_scan_request


def test_submitted_supplement_scan_witness_reopens_exact_question_only() -> None:
    request = supplement_scan_request(offline_openai_backend, "impact_assessment")
    question = request.question
    assert isinstance(question, SupplementQuestion), "fixture freezes a supplement question"
    scan = question.ctx.scan
    assert scan is not None and scan.scan_input is not None, "fixture supplies exact PDF bytes"
    validate_provider_scan_attachment(request, "openai")
    manifest = capture_frozen_request_manifest(request, "openai")
    assert manifest is not None, "fixture uses the actual provider envelope"
    source_url = "https://api.sejm.gov.pl/sejm/term10/prints/3039-A/3039-A.pdf"
    intent = BatchIntent.from_request(
        request=request,
        meta=DigestItemMeta(
            memo_key=request.custom_id,
            title=question.ctx.document_title,
            source_kind=question.ctx.source_kind,
            source_url=source_url,
            document_number="3039-A",
            scan_input=scan.scan_input,
        ),
        provider="openai",
        created_at=NOW,
    )
    item = LlmBatchItem.from_intent("scan-reader", intent)
    assert isinstance(item.meta, DigestItemMeta), "fixture uses secondary metadata"
    legacy = item.meta.model_dump_json()
    assert batch_item_meta("supplement", legacy) == item.meta
    assert "supplement_scan_input" not in legacy
    witness = SupplementScanInputWitness(
        term=request.term,
        document_number="3039-A",
        source_url=source_url,
        request_sha256=manifest.request_sha256,
        manifest_sha256=manifest.manifest_sha256,
        question_sha256=hashlib.sha256(
            canonical_json(question.model_dump(mode="json")).encode()
        ).hexdigest(),
        context_json=canonical_json(question.ctx.model_dump(mode="json", exclude={"scan"})),
        scan_input=scan.scan_input,
    )
    assert witness.restore_context(scan) == question.ctx
    assert scan.data not in witness.model_dump_json()
    with pytest.raises(ValueError, match="attached PDF"):
        witness.restore_context(scan.model_copy(update={"data": "YQ=="}))
    with pytest.raises(ValueError, match="invalid_supplement_scan_input"):
        witness.model_copy(update={"question_sha256": "f" * 64}).restore_context(scan)
    with pytest.raises(ValueError, match="invalid_supplement_scan_input"):
        SupplementScanInputWitness.model_validate(
            {
                **witness.model_dump(),
                "scan_input": scan.scan_input.model_copy(update={"transformations": ("triage",)}),
            }
        )

    metadata = item.meta.model_dump()
    metadata.update(frozen_request_manifest=manifest, supplement_scan_input=witness)
    valid = LlmBatchItem.model_validate({**item.model_dump(), "meta": metadata})
    assert isinstance(valid.meta, DigestItemMeta), "reader preserves typed submitted metadata"
    assert valid.meta.supplement_scan_input == witness
    with pytest.raises(ValueError, match="submitted supplements"):
        batch_item_meta("supplement", valid.meta.model_dump_json(), submitted=False)
    with pytest.raises(ValueError, match="submitted supplements"):
        batch_item_meta("amendments", valid.meta.model_dump_json())
    with pytest.raises(ValueError, match="invalid_supplement_scan_input"):
        LlmBatchItem.model_validate({**valid.model_dump(), "term": request.term + 1})
    for change in (
        {"request_sha256": "f" * 64},
        {"document_number": "3039-B"},
        {"source_url": "https://api.sejm.gov.pl/sejm/other.pdf"},
        {"frozen_request_manifest": manifest.model_copy(update={"options_sha256": "f" * 64})},
    ):
        with pytest.raises(ValueError, match="invalid_supplement_scan_input"):
            DigestItemMeta.model_validate({**metadata, **change})

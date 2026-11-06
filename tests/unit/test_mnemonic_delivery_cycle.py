import json
from pathlib import Path

import pytest
from tools.mnemonic_delivery_cycle import audit

from lexinform.models import (
    AnalysisRecord,
    BatchResult,
    Mnemonic,
    ResultApplication,
    ResultApplicationTarget,
)
from tests.unit.test_content_sources import Case
from tests.unit.test_content_sources import case as case
from tests.unit.test_input_receipts import NOW
from tests.unit.test_primary_delivery_cycle import card_snapshot


@pytest.mark.parametrize(
    ("control", "status"),
    [
        ("matching", "recorded"),
        ("compacted_answer", "recorded"),
        ("wrong_mnemonic", "invalid"),
        ("wrong_request", "invalid"),
        ("wrong_answer", "invalid"),
        ("missing_projection", "not_observed"),
    ],
)
def test_mnemonic_chain_requires_exact_frozen_binding(
    case: Case, tmp_path: Path, control: str, status: str
) -> None:
    paid = case.paid
    record = paid.record
    assert isinstance(record, AnalysisRecord), "the fixture supplies a paid analysis"
    proposal = record.analysis.model_copy(
        update={
            "mnemonic_candidate": "Fixture mnemonic",
            "mnemonic_basis": "Fixture basis",
            "mnemonic_language": "en",
        }
    )
    paid_record = record.model_copy(update={"analysis": proposal})
    paid = paid.model_copy(update={"record": paid_record})
    case.execute(
        "UPDATE computation_results SET result_json=? WHERE result_id=?",
        (paid.model_dump_json(), paid.result_id),
    )
    mnemonic = Mnemonic(
        name="Different mnemonic" if control == "wrong_mnemonic" else proposal.mnemonic_candidate,
        basis=proposal.mnemonic_basis,
        language=proposal.mnemonic_language,
        selected_at=NOW,
        computation_id=paid.result_id,
        request_sha256=paid.request_sha256,
    )
    case.bill.editorial = case.bill.editorial.model_copy(update={"mnemonic": mnemonic})
    if control != "missing_projection":
        case.repo.save_result_application(
            ResultApplication(
                result_id=paid.result_id,
                term=paid.term,
                number=paid.number,
                generation=None,
                target=ResultApplicationTarget(kind="editorial", ref="mnemonic"),
                phase="projection",
                outcome="applied",
                reason="retained_result",
                observed_generation=None,
                observed_status=None,
                created_at=NOW,
            )
        )
    answer = (
        proposal.model_copy(update={"mnemonic_basis": "Different basis"})
        if control == "wrong_answer"
        else proposal
    )
    case.execute(
        "UPDATE llm_batch_items SET result_json=? WHERE batch_id=? AND custom_id=?",
        (
            BatchResult(
                custom_id=paid.custom_id,
                answer=None if control == "compacted_answer" else answer,
                model=paid.model,
                input_tokens=paid.usage.batch_input,
                output_tokens=paid.usage.batch_output,
                cache_read_input_tokens=paid.usage.batch_cache_read,
                cache_creation_input_tokens=paid.usage.batch_cache_creation,
            ).model_dump_json(),
            paid.batch_id,
            paid.custom_id,
        ),
    )
    if control == "wrong_request":
        case.execute(
            "UPDATE llm_batch_items SET meta_json=json_set(meta_json,'$.request_sha256',?)",
            ("f" * 64,),
        )
    database = tmp_path / "mnemonic.db"
    publication_id = card_snapshot(case, database)
    before = database.read_bytes()

    report = audit(database, publication_id)

    assert report["status"] == status
    assert database.read_bytes() == before
    if status == "recorded":
        cycle = report["cycle"]
        assert isinstance(cycle, dict), "recorded evidence includes the scoped chain"
        assert cycle["result_id"] == paid.result_id
        assert cycle["retained_provider_answer"] == (
            "not_retained" if control == "compacted_answer" else "exact_retained_answer"
        )
        assert "Fixture mnemonic" not in json.dumps(report)
        assert "DO-NOT-EMIT-FROZEN-CARD" not in json.dumps(report)

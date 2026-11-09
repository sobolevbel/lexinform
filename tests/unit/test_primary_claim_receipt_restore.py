import sqlite3
from contextlib import closing

import pytest

from lexinform.adapters.primary_claim_receipts import CheckedPrimaryClaimReceipt
from lexinform.adapters.sqlite_repo import SCHEMA_VERSION, SqliteBillRepository
from lexinform.models.claim_evidence import PrimaryClaimTechnicalReceipt
from lexinform.models.computation_inputs import ComputationInputBinding
from tests.unit.test_primary_claim_citation_reader import primary_result
from tests.unit.test_primary_claim_technical_receipt import receipt_and_saved


def add_receipt(
    dump: str, receipt: PrimaryClaimTechnicalReceipt, *, receipt_id: str | None = None
) -> str:
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript(dump)
        db.execute(
            "INSERT INTO primary_claim_technical_receipts VALUES (?,?,?)",
            (receipt_id or receipt.receipt_id, receipt.result_id, receipt.model_dump_json()),
        )
        return "\n".join(db.iterdump()) + f"\nPRAGMA user_version = {SCHEMA_VERSION};\n"


def test_receipt_restore_preserves_unavailable_input_and_the_original_result() -> None:
    receipt, _ = receipt_and_saved()
    result = primary_result()
    binding = ComputationInputBinding(term=result.term, number=result.number, reuse_key="old")
    result = result.model_copy(update={"input_binding": binding})
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_computation_result(result)
        assert not repo.list_primary_claim_technical_receipts(result.result_id)
        dump = add_receipt(repo.dump(), receipt)
        repo.restore(dump)
        assert repo.dump() == dump
        assert repo.get_computation_result(result.result_id) == result
        (checked,) = repo.list_primary_claim_technical_receipts(result.result_id)
        assert isinstance(checked, CheckedPrimaryClaimReceipt)
        assert checked.receipt == receipt and checked.verification == "submitted_input_unavailable"


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("id", "identity or request mismatch"),
        ("request", "identity or request mismatch"),
        ("result", "requires its exact paid result"),
        ("claim", "exact retained claim"),
        ("range", "escapes its saved request length"),
    ],
)
def test_invalid_technical_receipt_leaves_original_dump(damage: str, message: str) -> None:
    receipt, _ = receipt_and_saved()
    result = primary_result()
    binding = ComputationInputBinding(term=result.term, number=result.number, reuse_key="old")
    result = result.model_copy(update={"input_binding": binding})
    wrong_id = None
    if damage == "id":
        wrong_id = "primary-claim-technical-v1:" + "0" * 64
    elif damage == "request":
        receipt = receipt.model_copy(update={"request_sha256": "b" * 64})
    elif damage == "result":
        receipt = receipt.model_copy(update={"result_id": "result-v1:" + "c" * 64})
    elif damage == "claim":
        evidence = receipt.evidence.model_copy(update={"claim_text_sha256": "d" * 64})
        receipt = receipt.model_copy(update={"evidence": evidence})
    else:
        receipt = receipt.model_copy(
            update={"submitted_span": (100, 100 + len(receipt.evidence.citation.quote))}
        )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_computation_result(result)
        original = repo.dump()
        with pytest.raises(ValueError, match=message):
            repo.restore(add_receipt(original, receipt, receipt_id=wrong_id))
        assert repo.dump() == original


def test_restore_rejects_more_than_the_bounded_number_of_receipts() -> None:
    receipt, _ = receipt_and_saved()
    result = primary_result()
    binding = ComputationInputBinding(term=result.term, number=result.number, reuse_key="old")
    result = result.model_copy(update={"input_binding": binding})
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_computation_result(result)
        original = repo.dump()
        with closing(sqlite3.connect(":memory:")) as db:
            db.executescript(original)
            for index in range(33):
                evidence = receipt.evidence.model_copy(
                    update={"source_text_sha256": f"{index:064x}"}
                )
                value = receipt.model_copy(
                    update={
                        "submitted_text_sha256": evidence.source_text_sha256,
                        "evidence": evidence,
                    }
                )
                db.execute(
                    "INSERT INTO primary_claim_technical_receipts VALUES (?,?,?)",
                    (value.receipt_id, value.result_id, value.model_dump_json()),
                )
            oversized = "\n".join(db.iterdump()) + f"\nPRAGMA user_version = {SCHEMA_VERSION};\n"
        with pytest.raises(ValueError, match="too many technical claim receipts"):
            repo.restore(oversized)
        assert repo.dump() == original

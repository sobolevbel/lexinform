import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from tools import audit_saved_primary_claims as audit
from tools.audit_saved_primary_claims import ReviewInputCheckpoint, SavedClaimReview
from tools.corpus_artifacts import digest

from lexinform.models import AnalysisRecord
from lexinform.models.claim_evidence import (
    MAX_PRIMARY_CLAIM_RECEIPT_BYTES,
    PrimaryClaimCitation,
    PrimaryClaimTechnicalReceipt,
)
from lexinform.models.reviews import EvidenceCitation
from tests.unit.test_primary_claim_citation_reader import primary_result

QUOTE = "Art. 6. Ustawa wchodzi w życie."
TEXT = "Tekst przygotowany. " + QUOTE
SPAN = (len(TEXT) - len(QUOTE), len(TEXT))


def receipt_and_saved() -> tuple[PrimaryClaimTechnicalReceipt, SavedClaimReview]:
    result = primary_result()
    record = result.record
    assert isinstance(record, AnalysisRecord) and result.request_sha256 is not None, (
        "the fixture keeps the primary answer and its original request"
    )
    text_sha = digest(TEXT.encode())
    receipt = PrimaryClaimTechnicalReceipt(
        result_id=result.result_id,
        request_sha256=result.request_sha256,
        submitted_text_sha256=text_sha,
        submitted_span=SPAN,
        evidence=PrimaryClaimCitation(
            field="key_changes",
            index=1,
            claim_text_sha256=digest(record.analysis.key_changes[1].encode()),
            source_text_sha256=text_sha,
            citation=EvidenceCitation(
                document_id="submitted-context", locator="Art. 6", quote=QUOTE
            ),
        ),
    )
    return receipt, SavedClaimReview(record, result.number, result.request_sha256, text_sha, TEXT)


def test_technical_receipt_has_bounded_stable_identity() -> None:
    receipt, _ = receipt_and_saved()
    raw = receipt.model_dump_json()
    decoded = PrimaryClaimTechnicalReceipt.read(raw)
    assert decoded == receipt and decoded.receipt_id == receipt.receipt_id
    assert PrimaryClaimTechnicalReceipt.read(" " + raw).receipt_id == receipt.receipt_id

    with pytest.raises(ValueError, match="UTF-8 byte limit"):
        PrimaryClaimTechnicalReceipt.read(raw + " " * MAX_PRIMARY_CLAIM_RECEIPT_BYTES)
    with pytest.raises(ValueError, match="exact submitted text range"):
        PrimaryClaimTechnicalReceipt.read(
            receipt.model_copy(update={"submitted_span": (SPAN[0], SPAN[1] + 1)}).model_dump_json()
        )


def test_technical_receipt_rechecks_exact_parent_and_literal_range(tmp_path: Path) -> None:
    receipt, saved = receipt_and_saved()

    @contextmanager
    def opened(
        database: Path,
        *,
        database_sha256: str,
        result_id: str,
        input_checkpoint: ReviewInputCheckpoint | None,
    ) -> Iterator[tuple[SavedClaimReview, sqlite3.Connection, None]]:
        assert (database, database_sha256, result_id, input_checkpoint) == (
            tmp_path,
            "c" * 64,
            receipt.result_id,
            None,
        )
        with closing(sqlite3.connect(":memory:")) as connection:
            yield saved, connection, None

    def verify(value: PrimaryClaimTechnicalReceipt) -> None:
        audit.verify_primary_claim_technical_receipt(
            tmp_path, database_sha256="c" * 64, result_id=receipt.result_id, receipt=value
        )

    with patch.object(audit, "_open_saved_review_input", opened):
        verify(receipt)
        with pytest.raises(ValueError, match="different result"):
            verify(receipt.model_copy(update={"result_id": "result-v1:" + "c" * 64}))
        with pytest.raises(ValueError, match="different input checkpoint"):
            verify(receipt.model_copy(update={"input_checkpoint_sha256": "e" * 64}))
        with pytest.raises(ValueError, match="exact saved request"):
            verify(receipt.model_copy(update={"request_sha256": "b" * 64}))
        with pytest.raises(ValueError, match="exact literal citation"):
            verify(receipt.model_copy(update={"submitted_span": (SPAN[0] - 1, SPAN[1] - 1)}))

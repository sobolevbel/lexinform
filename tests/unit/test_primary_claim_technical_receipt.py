import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Literal
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
    PrimaryComparisonCitation,
    PrimaryComparisonTechnicalReceipt,
)
from lexinform.models.input_receipts import MultipartTextInputReceipt, input_map_sha256
from lexinform.models.prepared_input import PreparedTextPart, PreparedTextSegment
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


@pytest.mark.parametrize("role", ["normative_annex", "osr", "unknown"])
def test_blockless_citation_requires_a_normative_source_part(
    role: Literal["normative_annex", "osr", "unknown"], tmp_path: Path
) -> None:
    receipt, saved = receipt_and_saved()
    parts = tuple(
        PreparedTextPart(text_sha256=digest(text.encode()), chars=len(text), role=part_role)
        for text, part_role in ((TEXT[: SPAN[0]], "bill"), (QUOTE, role))
    )
    segments = tuple(
        PreparedTextSegment(
            start=left,
            end=right,
            origin="source",
            part_index=index,
            source_start=0,
            source_end=right - left,
        )
        for index, (left, right) in enumerate(((0, SPAN[0]), SPAN))
    )
    prepared = MultipartTextInputReceipt(
        profile="test-multipart",
        source_selection_id="selection-v1:" + "a" * 64,
        source_parts=parts,
        context_sha256="b" * 64,
        request_sha256=saved.request_sha256,
        text_sha256=saved.text_sha256,
        text_chars=len(TEXT),
        segments=segments,
        full_map_sha256=input_map_sha256(segments),
    )

    @contextmanager
    def opened(
        _database: Path,
        **_: object,
    ) -> Iterator[tuple[SavedClaimReview, sqlite3.Connection, MultipartTextInputReceipt]]:
        with closing(sqlite3.connect(":memory:")) as connection:
            yield saved, connection, prepared

    with patch.object(audit, "_open_saved_review_input", opened):
        if role == "normative_annex":
            audit.verify_primary_claim_technical_receipt(
                tmp_path, database_sha256="c" * 64, result_id=receipt.result_id, receipt=receipt
            )
        else:
            with pytest.raises(ValueError, match="requires a normative source span"):
                audit.verify_primary_claim_technical_receipt(
                    tmp_path, database_sha256="c" * 64, result_id=receipt.result_id, receipt=receipt
                )


def test_comparison_receipt_requires_two_distinct_ordered_scopes() -> None:
    _, saved = receipt_and_saved()
    result_id = saved.record.computation_id
    assert result_id is not None, "the fixture retains a paid result identity"
    current = PrimaryComparisonCitation(
        result_id=result_id,
        request_sha256=saved.request_sha256,
        submitted_text_sha256=saved.text_sha256,
        submitted_span=SPAN,
        citation=EvidenceCitation(document_id="submitted-context", locator="Art. 6", quote=QUOTE),
    )
    previous = PrimaryComparisonCitation(
        result_id="result-v1:" + "b" * 64,
        request_sha256="c" * 64,
        submitted_text_sha256="d" * 64,
        submitted_span=(0, len(QUOTE)),
        citation=EvidenceCitation(
            document_id="previous-submitted-context", locator="Art. 6", quote=QUOTE
        ),
    )
    receipt = PrimaryComparisonTechnicalReceipt(
        field="changes_since_previous",
        index=0,
        claim_text_sha256=digest(b"A change"),
        before=previous,
        after=current,
    )
    answer = saved.record.analysis.model_copy(update={"changes_since_previous": ["A change"]})
    receipt.require_claim(answer)
    with pytest.raises(ValueError, match="exact retained change claim"):
        receipt.require_claim(answer.model_copy(update={"changes_since_previous": ["Other"]}))
    raw = receipt.model_dump_json()
    assert PrimaryComparisonTechnicalReceipt.read(" " + raw).receipt_id == receipt.receipt_id
    with pytest.raises(ValueError, match="UTF-8 byte limit"):
        PrimaryComparisonTechnicalReceipt.read(raw + " " * MAX_PRIMARY_CLAIM_RECEIPT_BYTES)
    with pytest.raises(ValueError, match="distinct before and after"):
        PrimaryComparisonTechnicalReceipt.read(
            receipt.model_copy(update={"before": current, "after": previous}).model_dump_json()
        )

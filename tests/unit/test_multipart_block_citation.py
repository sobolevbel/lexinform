import sqlite3
from contextlib import closing

import pytest
from tools.replay_multipart_claim_input import verify_prepared_block_copy

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import AnalysisQuestion
from lexinform.models.input_receipts import RclArchiveInputReceipt
from lexinform.services.input_receipts import with_block_companion
from tests.unit.test_rcl_archive_input_receipt import captured_source, offered


def test_claim_must_match_the_retained_block_copy_and_original_offset(
    repo: SqliteBillRepository,
) -> None:
    intent, receipt = offered(repo)
    source, _ = captured_source()
    question = intent.request.question
    assert isinstance(question, AnalysisQuestion), "the fixture prepares primary text"
    text = question.ctx.text
    enriched = with_block_companion(receipt, source, text)
    assert isinstance(enriched, RclArchiveInputReceipt) and enriched.block_companion is not None
    (copy,) = enriched.block_companion.rows
    span = (copy.start + 1, copy.start + 10)

    with closing(sqlite3.connect(":memory:")) as db:
        db.row_factory = sqlite3.Row
        db.executescript(repo.dump())
        proof = verify_prepared_block_copy(
            db,
            enriched,
            part_index=0,
            source_text=text,
            input_text=text,
            source_span=span,
            input_span=span,
        )

        assert proof["block_span"] == [1, 10]
        assert proof["witness_id"] == copy.witness_id
        with pytest.raises(ValueError, match="exact retained text"):
            verify_prepared_block_copy(
                db,
                enriched,
                part_index=0,
                source_text=text,
                input_text=text,
                source_span=(span[0] + 1, span[1] + 1),
                input_span=span,
            )
        with pytest.raises(ValueError, match="no single retained block copy"):
            verify_prepared_block_copy(
                db,
                receipt,
                part_index=0,
                source_text=text,
                input_text=text,
                source_span=span,
                input_span=span,
            )

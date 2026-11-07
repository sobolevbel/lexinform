import hashlib
import json

import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.errors import PreparedInputParentError
from lexinform.models import AnalysisQuestion, BatchIntent
from lexinform.models.block_input import (
    BlockCopyRun,
    BlockCopyWitness,
    PreparedBlockCompanion,
    PreparedBlockCopy,
)
from lexinform.models.extractions import ExtractionResult
from lexinform.models.input_receipts import RclArchiveInputReceipt
from tests.unit.test_document_candidates import BILL
from tests.unit.test_input_receipts import NOW
from tests.unit.test_physical_extraction import docx, extractor
from tests.unit.test_rcl_archive_input_receipt import offered


def prepared(repo: SqliteBillRepository) -> tuple[BatchIntent, BlockCopyWitness]:
    intent, receipt = offered(repo)
    extraction = repo.get_extraction_result(receipt.extraction_id)
    assert extraction is not None, "the native ZIP fixture saved its extraction"
    summary = next(
        p
        for p in extraction.selected_parts
        if p.physical_index == receipt.source_parts[0].physical_index
    )
    assert summary.manifest_sha256 is not None, "native Word extraction carries its body manifest"
    assert isinstance(intent.request.question, AnalysisQuestion), (
        "the fixture freezes primary analysis"
    )
    text = intent.request.question.ctx.text
    physical = extractor().extract_parts(
        docx(BILL + "Art. 2. Wchodzi w życie po upływie 21 dni." * 15)
    )
    native = physical.word_blocks[0].extraction
    note = physical.word_notes[0].extraction
    witness = BlockCopyWitness(
        manifest_sha256=summary.manifest_sha256,
        body_manifest_sha256=native.manifest_sha256,
        notes_manifest_sha256=note.manifest_sha256,
        xml_sha256=native.xml_sha256,
        part_sha256=summary.sha256,
        text_sha256=summary.text_sha256,
        text_chars=summary.text_chars,
        runs=(
            BlockCopyRun(
                source_start=0,
                source_end=20,
                block_id="word-xml:word/document.xml:0",
                block_start=0,
                block_end=20,
                block_chars=summary.text_chars,
                block_text_sha256=summary.text_sha256,
                text_sha256=hashlib.sha256(text[:20].encode()).hexdigest(),
            ),
        ),
    )
    enriched = ExtractionResult.model_validate(
        {
            **extraction.model_dump(),
            "selected_parts": tuple(
                p.model_copy(update={"block_copy_witness": witness}) if p == summary else p
                for p in extraction.selected_parts
            ),
        }
    )
    repo.save_extraction_result(enriched, recorded_at=NOW)
    references = repo.list_selection_extractions(receipt.source_selection_id)
    reference = next(r for r in references if r.extraction_id == extraction.result_id)
    repo.save_selection_extraction(
        reference.model_copy(update={"extraction_id": enriched.result_id})
    )
    row = PreparedBlockCopy(
        start=0,
        end=20,
        part_index=0,
        extraction_id=enriched.result_id,
        physical_index=summary.physical_index,
        witness_id=witness.witness_id,
        run_index=0,
        text_sha256=witness.runs[0].text_sha256,
    )
    digest = hashlib.sha256(("[" + row.model_dump_json() + "]").encode()).hexdigest()
    companion = PreparedBlockCompanion(
        retention="complete", observed_rows=1, rows_sha256=digest, rows=(row,)
    )
    receipt = RclArchiveInputReceipt.model_validate(
        {**receipt.model_dump(), "extraction_id": enriched.result_id, "block_companion": companion}
    )
    return BatchIntent.model_validate(
        {
            **intent.model_dump(),
            "meta": intent.meta.model_copy(update={"prepared_input_receipt": receipt}),
        }
    ), witness


def test_block_companion_is_used_by_saved_receipt_restore_and_legacy_ids(
    repo: SqliteBillRepository,
) -> None:
    legacy, _ = offered(repo)
    assert legacy.meta.prepared_input_receipt is not None, "fixture has its legacy receipt"
    assert "block_companion" not in legacy.meta.prepared_input_receipt.model_dump()
    exact = legacy.model_dump_json()
    assert BatchIntent.model_validate_json(exact).model_dump_json() == exact
    intent, _ = prepared(repo)
    repo.save_batch_intent(intent)
    dump = repo.dump()
    for _ in range(2):
        repo.restore(dump)
        assert repo.dump() == dump and repo.list_queued_batch_intents() == [intent]
    invalid = dump.replace('"block_copy_witness":', '"unknown_block_witness":')
    with pytest.raises(ValueError):
        repo.restore(invalid)
    assert repo.dump() == dump


@pytest.mark.parametrize("fault", ["missing", "witness", "shift", "substring", "generated_note"])
def test_block_companion_rejects_uncorroborated_parent_and_subranges(
    repo: SqliteBillRepository, fault: str
) -> None:
    intent, _ = prepared(repo)
    receipt = intent.meta.prepared_input_receipt
    assert isinstance(receipt, RclArchiveInputReceipt) and receipt.block_companion is not None
    companion = receipt.block_companion
    row = companion.rows[0]
    changes: dict[str, object]
    if fault == "missing":
        changes = {"extraction_id": "extraction-v1:" + "0" * 64}
    elif fault == "witness":
        changes = {"witness_id": "block-copy-v1:" + "0" * 64}
    elif fault == "shift":
        changes = {"start": 20, "end": 40}
    elif fault == "substring":
        changes = {"end": 19}
    else:
        changes = {"end": 25}
    row = row.model_copy(update=changes)
    values = companion.model_dump()
    values.update(
        rows=(row,),
        rows_sha256=hashlib.sha256(("[" + row.model_dump_json() + "]").encode()).hexdigest(),
    )
    bad = receipt.model_copy(
        update={"block_companion": PreparedBlockCompanion.model_validate(values)}
    )
    before = repo.dump()
    with pytest.raises((PreparedInputParentError, ValidationError, ValueError)):
        malformed = BatchIntent.model_validate(
            {
                **intent.model_dump(),
                "meta": intent.meta.model_copy(update={"prepared_input_receipt": bad}),
            }
        )
        repo.save_batch_intent(malformed)
    assert repo.dump() == before


def test_prefix_declares_omissions_and_preserves_only_verified_rows(
    repo: SqliteBillRepository,
) -> None:
    intent, _ = prepared(repo)
    raw = json.loads(intent.model_dump_json())
    companion = raw["meta"]["prepared_input_receipt"]["block_companion"]
    companion.update(retention="prefix", observed_rows=2, rows_sha256="0" * 64)
    repo.save_batch_intent(BatchIntent.model_validate(raw))
    saved = repo.list_queued_batch_intents()[0].meta.prepared_input_receipt
    assert saved is not None, "reader retains the optional companion"
    assert saved.physical_origins == "unmapped"


@pytest.mark.parametrize("scope", ["block", "part"])
def test_full_extent_copy_hashes_cannot_contradict_their_parent(
    scope: str, repo: SqliteBillRepository
) -> None:
    _, witness = prepared(repo)
    run = witness.runs[0]
    if scope == "block":
        with pytest.raises(ValidationError, match="whole block copy"):
            BlockCopyRun.model_validate(
                {**run.model_dump(), "block_chars": run.block_end, "block_text_sha256": "0" * 64}
            )
    else:
        with pytest.raises(ValidationError, match="whole part copy"):
            BlockCopyWitness.model_validate(
                {**witness.model_dump(), "text_chars": run.source_end, "text_sha256": "0" * 64}
            )


def test_repeated_block_parent_identity_cannot_conflict(repo: SqliteBillRepository) -> None:
    _, witness = prepared(repo)
    row = witness.runs[0]
    shifted = row.model_copy(
        update={"source_start": 20, "source_end": 40, "block_text_sha256": "0" * 64}
    )
    with pytest.raises(ValidationError, match="repeated block IDs"):
        BlockCopyWitness.model_validate({**witness.model_dump(), "runs": (row, shifted)})

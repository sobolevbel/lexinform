import hashlib
from dataclasses import replace

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AmendmentInputReceipt,
    AnalysisRecord,
    BillStatus,
    DigestItemMeta,
    ProcessDetail,
    TextDocument,
)
from lexinform.sections import TextBudget
from lexinform.services.analysis import AnalysisOptions, AnalysisService, Waiting
from lexinform.services.document_history import PhysicalSourceRead, SourceReadCapture
from lexinform.services.documents import TextLoader
from lexinform.services.input_receipts import capture_secondary_input_receipt
from lexinform.services.sources import MetadataOnlySource, TextSources
from lexinform.text_projection import SourcePiece, project_pieces
from tests.fakes import FakeLlm, FixedClock, make_analysis
from tests.unit.test_amendment_input_receipts import request
from tests.unit.test_input_receipts import NOW, SOURCE
from tests.unit.test_physical_extraction import docx, extractor
from tests.unit.test_primary_input_receipt_capture import captured
from tests.unit.test_supplement_input_capture import Case, CountingBatch


@pytest.mark.parametrize(
    "fault", ["none", "missing", "no_extraction", "physical", "projection", "package"]
)
def test_shared_secondary_capture_requires_actual_corroborated_single_source(fault: str) -> None:
    selected, source = captured()
    text = selected.parts[0].text
    actual: SourceReadCapture | None = source if fault != "missing" else None
    projection = project_pieces(text, (SourcePiece(text, 0),)) if fault != "projection" else None
    if fault == "no_extraction":
        actual = replace(source, extractions=())
    elif fault == "physical":
        actual = PhysicalSourceRead(source.artifacts, extractions=source.extractions)
    elif fault == "package":
        selected = selected.model_copy(
            update={"parts": (selected.parts[0].model_copy(update={"members": ("inner.docx",)}),)}
        )
    receipt = capture_secondary_input_receipt(request(text), selected, projection, actual)
    if fault == "none":
        assert isinstance(receipt, AmendmentInputReceipt)
        assert receipt.source_selection_id == source.selection.selection_id
        assert receipt.extraction_id == source.extractions[0].result_id
    else:
        assert receipt is None


def amendment_case(repo: SqliteBillRepository, process: ProcessDetail) -> Case:
    repo.upsert_summary(process, now=NOW)
    repo.save_analysis(
        process.term,
        process.number,
        AnalysisRecord(
            analysis=make_analysis(),
            model="prior",
            prompt_version="prior",
            input_chars=1,
            truncated=False,
            text_source="pdf",
            created_at=NOW,
        ),
    )
    repo.set_status(process.term, process.number, BillStatus.ANALYZED)
    bill = repo.get(process.term, process.number)
    assert bill is not None, "the fixture enrolls an analyzed bill"
    batch = CountingBatch()

    def read(url: str, *, max_bytes: int | None = None) -> bytes:
        return docx(SOURCE * 40 if url.endswith("main.docx") else "USTAWA recommendation " * 80)

    service = AnalysisService(
        repo,
        TextSources(MetadataOnlySource()),
        TextLoader({"example.test": read}, extractor(), max_bytes=32_000_000),
        FakeLlm(),
        FixedClock(NOW),
        AnalysisOptions(
            require_batch=True, batch_provider="openai", batch_models={"amendments": "fake"}
        ),
        batch=batch,
        text_budget=TextBudget(60000),
    )
    return Case(
        service,
        batch,
        bill,
        TextDocument(url="https://example.test/main.docx", kind="committee_amendments"),
    )


def test_fresh_amendment_receipt_keeps_normative_parent_when_recommendation_is_loaded_last(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
) -> None:
    case = amendment_case(repo, process_3039)
    result = case.service.summarize_amendments(
        case.bill,
        case.document,
        proposal="Motion",
        recommendation=TextDocument(
            url="https://example.test/recommendation.docx", kind="committee_amendments"
        ),
        may_wait=True,
    )
    assert isinstance(result, Waiting) and len(case.batch.prepared) == 1
    (intent,) = repo.list_queued_batch_intents()
    assert isinstance(intent.meta, DigestItemMeta)
    receipt = intent.meta.prepared_input_receipt
    assert isinstance(receipt, AmendmentInputReceipt)
    ctx = intent.request.ctx
    assert receipt.text_sha256 == hashlib.sha256((SOURCE * 40).encode()).hexdigest()
    assert ctx.model_dump()["recommendation"] != ctx.model_dump()["text"]
    assert receipt.source_part.text_sha256 == receipt.text_sha256
    assert receipt.request_sha256 == intent.request.payload_sha256
    repo.restore(repo.dump())
    before = repo.dump()
    repeated = case.service.summarize_amendments(
        case.bill,
        case.document,
        proposal="Motion",
        recommendation=TextDocument(
            url="https://example.test/recommendation.docx", kind="committee_amendments"
        ),
        may_wait=True,
    )
    assert isinstance(repeated, Waiting) and len(case.batch.prepared) == 1
    assert repo.dump() == before


def test_resumed_context_does_not_borrow_latest_capture(
    repo: SqliteBillRepository,
    process_3039: ProcessDetail,
) -> None:
    case = amendment_case(repo, process_3039)
    context = case.service.amendments_context(case.bill, case.document)
    assert context is not None, "the source fixture has readable normative text"
    result = case.service.summarize_amendments(
        case.bill, case.document, context=context, may_wait=True
    )
    assert isinstance(result, Waiting) and len(case.batch.prepared) == 1
    assert repo.list_queued_batch_intents()[0].meta.prepared_input_receipt is None

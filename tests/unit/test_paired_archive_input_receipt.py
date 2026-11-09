import json
from contextlib import closing

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.document_input import append_self_amendments
from lexinform.errors import PreparedInputParentError
from lexinform.models import (
    AnalysisQuestion,
    BatchIntent,
    BatchRequest,
    BillContext,
    DocumentPart,
    DocumentSelection,
    DocumentSelectionRecord,
    SourceObservation,
    TextDocument,
)
from lexinform.models.input_receipts import (
    ArchiveInputPart,
    RclPairedArchiveInputReceipt,
    input_map_sha256,
    input_text_sha256,
    primary_context_sha256,
)
from lexinform.models.prepared_input import PreparedTextSegment
from tests.unit.test_input_receipts import NOW, SOURCE, meta, request


def test_paired_archive_intent_rejects_an_unlinked_source_observation() -> None:
    base_url = "https://example.test/base.zip"
    amendment_url = "https://example.test/amendment.zip"
    amendment_text = "Art. 2. Autopoprawka."
    selected = DocumentSelection(
        parts=(
            DocumentPart(
                role="bill",
                url=base_url,
                text=SOURCE,
                members=("draft.docx",),
                text_end=len(SOURCE),
                text_sha256=input_text_sha256(SOURCE),
            ),
            DocumentPart(
                role="self_amendment",
                url=amendment_url,
                text=amendment_text,
                members=("proposal.docx",),
                text_end=len(amendment_text),
                text_sha256=input_text_sha256(amendment_text),
            ),
        ),
    )
    source = DocumentSelectionRecord.from_selection(selected)
    text = append_self_amendments(SOURCE, selected.parts[1:])
    end = len(text) - len(amendment_text)
    segments = (
        PreparedTextSegment(
            start=0,
            end=len(SOURCE),
            origin="source",
            part_index=0,
            source_start=0,
            source_end=len(SOURCE),
        ),
        PreparedTextSegment(
            start=len(SOURCE),
            end=end,
            origin="generated",
            system_origin="input_transform",
        ),
        PreparedTextSegment(
            start=end,
            end=len(text),
            origin="source",
            part_index=1,
            source_start=0,
            source_end=len(amendment_text),
        ),
    )
    primary = request(text).ctx
    assert isinstance(primary, BillContext), "the request fixture uses a primary question"
    ctx = primary.model_copy(update={"source_kind": "rcl"})
    frozen = BatchRequest(
        custom_id="paired",
        term=10,
        number=ctx.number,
        question=AnalysisQuestion(kind="analysis", ctx=ctx),
    ).freeze(
        payload_json=json.dumps({"body": ctx.model_dump(mode="json")}),
        model="gpt-6.1-sol",
        prompt_version="pinned",
        estimated_cost_usd=0.01,
    )
    assert frozen.payload_sha256 is not None, "freeze binds the fixture payload"
    receipt = RclPairedArchiveInputReceipt(
        source_selection_id=source.selection_id,
        source_observation_id="source-observation-v1:" + "1" * 64,
        extraction_id="extraction-v1:" + "2" * 64,
        proposal_trace_id="candidate-trace-v1:" + "3" * 64,
        amendment_capture_id="4" * 64,
        amendment_ordinal=0,
        source_parts=(
            ArchiveInputPart(
                role="bill",
                chars=len(SOURCE),
                text_sha256=input_text_sha256(SOURCE),
                physical_index=0,
            ),
            ArchiveInputPart(
                role="self_amendment",
                chars=len(amendment_text),
                text_sha256=input_text_sha256(amendment_text),
                physical_index=0,
            ),
        ),
        context_sha256=primary_context_sha256(ctx),
        request_sha256=frozen.payload_sha256,
        text_sha256=input_text_sha256(text),
        text_chars=len(text),
        full_map_sha256=input_map_sha256(segments),
        segments=segments,
    )
    intent = BatchIntent.from_request(
        request=frozen,
        meta=meta().model_copy(
            update={
                "source_kind": "rcl",
                "source_url": base_url,
                "selection": selected,
                "input_chars": len(text),
                "prepared_input_receipt": receipt,
            }
        ),
        provider="openai",
        created_at=NOW,
    )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_source_observation(
            source,
            SourceObservation(
                term=10,
                number=ctx.number,
                purpose="analysis",
                recorded_at=NOW,
                document=TextDocument(kind="rcl", url=base_url),
                selection_id=source.selection_id,
            ),
        )
        before = repo.dump()
        with pytest.raises(
            PreparedInputParentError, match="input_receipt_archive_observation_mismatch"
        ):
            repo.save_batch_intent(intent)
        assert repo.dump() == before

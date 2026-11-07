import hashlib
from contextlib import closing

import pytest
from tools.replay_primary_claims import technical_answer

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    AnalysisQuestion,
    BatchIntent,
    DocumentPart,
    DocumentSelection,
    EvidenceCitation,
    PreparedTextSegment,
    ReviewDocument,
)
from lexinform.models.document_history import DocumentSelectionRecord
from lexinform.models.input_receipts import (
    LooseRclInputPart,
    SejmSelfAmendmentInputReceipt,
    input_map_sha256,
    input_text_sha256,
    primary_context_sha256,
)
from lexinform.primary_claim_input import _validate_key_change_input, validate_key_change_input
from lexinform.primary_claims import KeyChangeEvidence
from tests.unit.test_input_receipts import NOW, meta, request
from tests.unit.test_rcl_archive_input_receipt import offered


@pytest.mark.parametrize("control", ["exact", "wrong_member", "cross_part"])
def test_only_verified_archive_main_binding_enables_existing_literal_map(
    control: str,
) -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        intent, receipt = offered(repo)
        selected = repo.get_document_selection(receipt.source_selection_id)
    assert selected is not None, "the native fixture stores the exact source selection"
    part = selected.selection.parts[0]
    question = intent.request.question
    assert isinstance(question, AnalysisQuestion), "fixture queues a primary question"
    text = question.ctx.text
    start = text.index("Art. 1.")
    quote = text[start : start + 35].rstrip()
    document = ReviewDocument(
        identity=part.url,
        url=part.url,
        role="project_text",
        text=text,
        sha256=part.text_sha256,
        normalized_sha256=part.text_sha256,
        file_sha256="0" * 64 if control == "wrong_member" else part.sha256,
    )
    initial = technical_answer(text)
    record = initial.model_copy(
        update={
            "source_kind": "rcl",
            "source_url": part.url,
            "request_sha256": intent.request.payload_sha256,
        }
    )
    evidence = KeyChangeEvidence(
        0,
        hashlib.sha256(record.analysis.key_changes[0].encode()).hexdigest(),
        document.sha256,
        EvidenceCitation(document_id=part.url, quote=quote, locator="Art. 1"),
    )

    if control == "cross_part":
        cut = start + len(quote) - 1
        segments = (
            PreparedTextSegment(
                start=0, end=cut, origin="source", part_index=0, source_start=0, source_end=cut
            ),
            PreparedTextSegment(
                start=cut, end=cut + 1, origin="source", part_index=1, source_start=0, source_end=1
            ),
            PreparedTextSegment(
                start=cut + 1, end=len(text), origin="generated", system_origin="input_transform"
            ),
        )
        receipt = receipt.model_copy(
            update={"segments": segments, "full_map_sha256": input_map_sha256(segments)}
        )
        intent = intent.model_copy(
            update={"meta": intent.meta.model_copy(update={"prepared_input_receipt": receipt})}
        )

    default = validate_key_change_input(
        record, evidence, (document,), intent=intent, source_selection=selected
    )
    checked = _validate_key_change_input(
        record, evidence, (document,), intent=intent, verified_multipart_selection=selected
    )

    assert default.status == "unknown" and default.reason == "source_binding_unavailable"
    assert (
        checked.status
        == {"exact": "retained", "wrong_member": "unknown", "cross_part": "omitted"}[control]
    )
    assert checked.input_span == ((start, start + len(quote)) if control == "exact" else None)


@pytest.mark.parametrize(
    ("control", "status"),
    [
        ("exact", "retained"),
        ("wrong_index", "unknown"),
        ("wrong_hash", "unknown"),
        ("generated_gap", "omitted"),
    ],
)
def test_verified_self_amendment_maps_only_its_continuous_literal_span(
    control: str, status: str
) -> None:
    bill = "Original bill."
    amendment = "Art. 90. Wniosek składa się w terminie."
    gap = "|" if control == "generated_gap" else ""
    cutoff = 12
    prefix = f"{bill}|"
    text = prefix + amendment[:cutoff] + gap + amendment[cutoff:]
    frozen = request(text)
    question = frozen.question
    assert isinstance(question, AnalysisQuestion), "fixture queues a primary question"
    request_sha = frozen.payload_sha256
    assert request_sha is not None, "fixture has the frozen request identity"
    main = DocumentPart(
        url="https://example.test/2320",
        role="bill",
        sha256="a" * 64,
        text_sha256=input_text_sha256(bill),
        text_end=len(bill),
    )
    proposal = DocumentPart(
        url="https://example.test/2320-A",
        role="self_amendment",
        sha256="b" * 64,
        text_sha256=input_text_sha256(amendment),
        text_end=len(amendment),
    )
    selection = DocumentSelectionRecord.from_selection(DocumentSelection(parts=(main, proposal)))
    start, split = len(prefix), len(prefix) + cutoff
    segments: tuple[PreparedTextSegment, ...] = (
        PreparedTextSegment(
            start=0,
            end=len(bill),
            origin="source",
            part_index=0,
            source_start=0,
            source_end=len(bill),
        ),
        PreparedTextSegment(
            start=len(bill), end=start, origin="generated", system_origin="input_transform"
        ),
        PreparedTextSegment(
            start=start, end=split, origin="source", part_index=1, source_start=0, source_end=cutoff
        ),
    )
    if gap:
        segments += (
            PreparedTextSegment(
                start=split, end=split + 1, origin="generated", system_origin="input_transform"
            ),
        )
    segments += (
        PreparedTextSegment(
            start=split + len(gap),
            end=len(text),
            origin="source",
            part_index=1,
            source_start=cutoff,
            source_end=len(amendment),
        ),
    )
    receipt = SejmSelfAmendmentInputReceipt(
        source_selection_id=selection.selection_id,
        source_observation_id="source-observation-v1:" + "a" * 64,
        proposal_trace_id="candidate-trace-v1:" + "b" * 64,
        source_parts=(
            LooseRclInputPart(
                role="bill",
                text_sha256=main.text_sha256,
                chars=len(bill),
                extraction_id="extraction-v1:" + "a" * 64,
            ),
            LooseRclInputPart(
                role="self_amendment",
                text_sha256=proposal.text_sha256,
                chars=len(amendment),
                extraction_id="extraction-v1:" + "b" * 64,
            ),
        ),
        context_sha256=primary_context_sha256(question.ctx),
        request_sha256=request_sha,
        text_sha256=input_text_sha256(text),
        text_chars=len(text),
        full_map_sha256=input_map_sha256(segments),
        segments=segments,
    )
    intent = BatchIntent.from_request(
        request=frozen,
        meta=meta().model_copy(
            update={
                "selection": selection.selection,
                "source_url": main.url,
                "input_chars": len(text),
                "prepared_input_receipt": receipt,
            }
        ),
        provider="openai",
        created_at=NOW,
    )
    source = ReviewDocument(
        identity=proposal.url,
        url=proposal.url,
        role="project_text",
        text=amendment,
        sha256=proposal.text_sha256,
        normalized_sha256=proposal.text_sha256,
        file_sha256=main.sha256 if control == "wrong_hash" else proposal.sha256,
    )
    record = technical_answer(text).model_copy(
        update={"source_url": main.url, "request_sha256": request_sha}
    )
    evidence = KeyChangeEvidence(
        0,
        input_text_sha256(record.analysis.key_changes[0]),
        source.sha256,
        EvidenceCitation(document_id=source.identity, locator="Art. 90", quote=amendment),
    )

    checked = _validate_key_change_input(
        record,
        evidence,
        (source,),
        intent=intent,
        verified_multipart_selection=selection,
        multipart_part_index=0 if control == "wrong_index" else 1,
    )

    assert checked.status == status
    assert checked.input_span == ((start, len(text)) if control == "exact" else None)

import hashlib
from dataclasses import replace

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.errors import PreparedInputParentError
from lexinform.models import (
    AnalysisQuestion,
    BatchIntent,
    BatchRequest,
    BillContext,
    DocumentSelection,
    DocumentSelectionRecord,
    TextDocument,
)
from lexinform.models.extractions import extraction_metadata_sha256
from lexinform.models.input_receipts import (
    ArchiveInputPart,
    RclArchiveInputReceipt,
    input_map_sha256,
    primary_context_sha256,
)
from lexinform.models.prepared_input import PreparedTextSegment
from lexinform.sections import PAGE_BREAK
from lexinform.services.document_history import DocumentSourceRead, record_source_read
from lexinform.services.documents import TextLoader
from lexinform.services.input_receipts import capture_rcl_archive_input_receipt
from lexinform.text_projection import SourcePiece, project_pieces
from tests.fakes import FakeSejmGateway
from tests.unit.test_document_candidates import BILL
from tests.unit.test_input_receipts import NOW, meta, request
from tests.unit.test_physical_extraction import archive, docx, extractor

URL = "https://api.test/package.zip"


def captured_source() -> tuple[DocumentSourceRead, DocumentSelection]:
    body = archive(
        {
            "bill.docx": docx(BILL + "Art. 2. Wchodzi w życie po upływie 21 dni." * 15),
            "uzasadnienie.docx": docx("UZASADNIENIE\nProjekt reguluje zasady pobytu. " * 12),
        }
    )
    gateway = FakeSejmGateway(files={URL: body})
    loader = TextLoader({"api.test": gateway.download}, extractor(), max_bytes=100_000)
    document = TextDocument(url=URL, kind="rcl", compare_versions=True)
    selected, file = loader.select_document(document)
    source = loader.capture_source_read(document, selected, file)
    assert isinstance(source, DocumentSourceRead), "the fixture has an admitted root"
    return source, file.selection


def offered(
    repo: SqliteBillRepository, fault: str | None = None
) -> tuple[BatchIntent, RclArchiveInputReceipt]:
    source, live = captured_source()
    text = live.parts[0].text
    (extraction,) = source.extractions
    selection = source.selection.selection
    assert len(selection.parts) == 2, "the fixture selects a bill and its justification"
    recorded = []
    for part in selection.parts:
        (summary,) = [
            part_summary
            for part_summary in extraction.selected_parts
            if part_summary.member_path_sha256 == extraction_metadata_sha256(part.members)
        ]
        recorded.append(
            ArchiveInputPart(
                role=part.role,
                chars=summary.text_chars,
                text_sha256=summary.text_sha256,
                physical_index=summary.physical_index,
            )
        )
    if fault in ("path", "root", "hybrid", "legacy"):
        part = selection.parts[1]
        changes: dict[str, dict[str, str | tuple[str, ...]]] = {
            "path": {"members": ("other.docx",)},
            "root": {"container_sha256": "b" * 64},
            "hybrid": {"url": URL + "?other"},
            "legacy": {"members": ()},
        }
        selection = selection.model_copy(
            update={"parts": (selection.parts[0], part.model_copy(update=changes[fault]))}
        )
        source = replace(source, selection=DocumentSelectionRecord.from_selection(selection))
    record_source_read(repo, source, term=10, number="3039", purpose="analysis", recorded_at=NOW)
    (observation,) = repo.list_source_observations(10, "3039")
    assert len(text) == recorded[0].chars
    context = request().ctx
    assert isinstance(context, BillContext), "the fixture starts with a primary question"
    frozen = BatchRequest(
        custom_id="archive",
        term=10,
        number="3039",
        question=AnalysisQuestion(
            kind="analysis", ctx=context.model_copy(update={"source_kind": "rcl", "text": text})
        ),
    ).freeze(
        payload_json='{"fixture":"offline request"}',
        model="gpt-6.1-sol",
        prompt_version="fixture",
        estimated_cost_usd=0,
    )
    assert isinstance(frozen.question, AnalysisQuestion) and frozen.payload_sha256 is not None, (
        "the fixture has an exact frozen primary request"
    )
    segments = (
        PreparedTextSegment(
            start=0,
            end=len(text),
            origin="source",
            part_index=0,
            source_start=0,
            source_end=len(text),
        ),
    )
    receipt = RclArchiveInputReceipt(
        source_selection_id=source.selection.selection_id,
        source_observation_id=observation.observation_id,
        extraction_id=extraction.result_id
        if fault != "extraction"
        else "extraction-v1:" + "0" * 64,
        source_parts=(recorded[0], recorded[1]),
        context_sha256=primary_context_sha256(frozen.question.ctx),
        request_sha256=frozen.payload_sha256,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        text_chars=len(text),
        full_map_sha256=input_map_sha256(segments),
        segments=segments,
    )
    if fault == "slot":
        receipt = receipt.model_copy(
            update={
                "source_parts": (
                    recorded[0].model_copy(update={"physical_index": recorded[1].physical_index}),
                    recorded[1].model_copy(update={"physical_index": recorded[0].physical_index}),
                )
            }
        )
    intent = BatchIntent.from_request(
        request=frozen,
        provider="openai",
        created_at=NOW,
        meta=meta().model_copy(
            update={
                "source_kind": "rcl",
                "source_url": URL,
                "selection": selection.without_text(),
                "input_chars": len(text),
                "text": text,
            }
        ),
    )
    return intent.model_copy(
        update={"meta": intent.meta.model_copy(update={"prepared_input_receipt": receipt})}
    ), receipt


def test_exact_one_root_receipt_round_trips(repo: SqliteBillRepository) -> None:
    intent, receipt = offered(repo)
    repo.save_batch_intent(intent)
    dump = repo.dump()
    repo.restore(dump)
    repo.restore(dump)
    (loaded,) = repo.list_queued_batch_intents()
    assert loaded.meta.prepared_input_receipt == receipt
    assert repo.dump() == dump
    damaged = dump.replace("\nCOMMIT;", "\nDELETE FROM artifact_reads;\nCOMMIT;")
    assert damaged != dump
    with pytest.raises(ValueError, match="invalid_input_intent_snapshot"):
        repo.restore(damaged)
    assert repo.dump() == dump


@pytest.mark.parametrize("fault", ["slot", "path", "root", "hybrid", "legacy", "extraction"])
def test_wrong_archive_parents_leave_queue_unchanged(
    repo: SqliteBillRepository, fault: str
) -> None:
    intent, _ = offered(repo, fault)
    before = repo.dump()
    with pytest.raises((PreparedInputParentError, ValueError)):
        repo.save_batch_intent(intent)
    assert repo.dump() == before


def test_archive_version_receipt_requires_a_trace_link_on_its_observation(
    repo: SqliteBillRepository,
) -> None:
    intent, receipt = offered(repo)
    qualified = receipt.model_copy(
        update={"selected_version_trace_id": "candidate-trace-v1:" + "0" * 64}
    )
    offered_intent = intent.model_copy(
        update={"meta": intent.meta.model_copy(update={"prepared_input_receipt": qualified})}
    )
    before = repo.dump()
    with pytest.raises(PreparedInputParentError, match="input_receipt_parent_missing"):
        repo.save_batch_intent(offered_intent)
    assert repo.dump() == before
    assert "selected_version_trace_id" not in receipt.model_dump()


@pytest.mark.parametrize("fault", ["unknown", "gap", "checksum"])
def test_archive_receipt_rejects_unproved_coverage(repo: SqliteBillRepository, fault: str) -> None:
    _, receipt = offered(repo)
    segments = receipt.segments
    if fault == "unknown":
        segments = (PreparedTextSegment(start=0, end=receipt.text_chars, origin="unknown"),)
    elif fault == "gap":
        first = segments[0]
        segments = (first.model_copy(update={"start": 1, "source_start": 1}),)
    checksum = "0" * 64 if fault == "checksum" else input_map_sha256(segments)
    with pytest.raises(ValueError):
        RclArchiveInputReceipt.model_validate(
            {**receipt.model_dump(), "segments": segments, "full_map_sha256": checksum}
        )


@pytest.mark.parametrize(
    "fault", [None, "text_only_cache", "hybrid", "body_mismatch", "unproved_version"]
)
def test_writer_uses_the_actual_projection_and_exact_archive_parent(fault: str | None) -> None:
    source, selection = captured_source()
    text = PAGE_BREAK.join(part.text for part in selection.parts)
    projection = project_pieces(text, (SourcePiece(text, 0),))
    template = request().ctx
    assert isinstance(template, BillContext), "the fixture uses a primary bill context"
    context = template.model_copy(update={"source_kind": "rcl", "text": text})
    frozen = BatchRequest(
        custom_id="archive",
        term=10,
        number=context.number,
        question=AnalysisQuestion(kind="analysis", ctx=context),
    ).freeze(
        payload_json=context.model_dump_json(),
        model="gpt-6.1-sol",
        prompt_version="fixture",
        estimated_cost_usd=0,
    )
    if fault == "text_only_cache":
        source = replace(source, extractions=())
    elif fault == "hybrid":
        document = source.document.model_copy(update={"amendment_urls": (URL + "?amendment",)})
        source = replace(source, document=document)
    elif fault == "unproved_version":
        alternate = TextDocument(url=URL + "?alternate", kind="rcl")
        document = source.document.model_copy(update={"alternatives": (alternate,)})
        source = replace(source, document=document)
    elif fault == "body_mismatch":
        source = replace(source, reads=())

    captured = capture_rcl_archive_input_receipt(frozen, selection, projection, source)

    if fault is not None:
        assert captured is None
    else:
        assert captured is not None, "the ordinary ZIP has its precise recorded source and map"
        assert captured.request_sha256 == frozen.payload_sha256
        assert captured.text_chars == len(text)
        assert tuple(part.role for part in captured.source_parts) == ("bill", "justification")
        assert any(segment.origin == "generated" for segment in captured.segments)

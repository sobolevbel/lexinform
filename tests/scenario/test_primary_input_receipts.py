import sqlite3
import threading
from contextlib import closing
from datetime import timedelta
from typing import Literal
from unittest.mock import patch

import pytest

from lexinform.adapters import document_text as document_module
from lexinform.adapters.llm_prompts import build_user_prompt
from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    BatchIntent,
    BatchItemMeta,
    BatchRequest,
    BillContext,
    BillStatus,
    DocumentSelectionRecord,
    PrimaryInputReceipt,
    SelectionExtractionReference,
    SourceObservation,
    TextDocument,
)
from lexinform.models.input_receipts import LooseRclInputReceipt
from lexinform.services import analysis as module
from tests.harness import World, print_url, rcl_document, rcl_folder, rcl_project, rcl_stage
from tests.scenario.test_extraction_source_history import reader
from tests.unit.test_doc_note_extraction import source_notes
from tests.unit.test_doc_note_physical_parts import ole_document
from tests.unit.test_odt_blocks import odt_document
from tests.unit.test_physical_extraction import archive, docx

TEXT = "USTAWA\nArt. 1. Cudzoziemiec składa wniosek osobiście i otrzymuje 300 zł.\n" * 50


def world(data: bytes, *, workers: int = 1, numbers: tuple[str, ...] = ("4100",)) -> World:
    result = World(
        workers=workers,
        batch_only=True,
        analysis_model="gpt-6.1-sol",
        extractor=reader(),
        text_budget_chars=1000,
    )
    for number in numbers:
        result.add_bill(number, "Projekt ustawy o cudzoziemcach")
        result.gateway.files[print_url(number)] = data
    return result


@pytest.mark.parametrize("workers", [1, 4])
def test_primary_receipts_follow_actual_parent_checkpoint_and_prepare_once(
    workers: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = world(docx(TEXT), workers=workers, numbers=("4100", "4101", "4102"))
    caller = threading.get_ident()
    prepare = current.batch.prepare_request
    save_source = current.repo.save_source_observation
    save_intent = current.repo.save_batch_intent
    requests: list[BatchRequest] = []
    writes: list[tuple[str, str, int]] = []

    def counted(request: BatchRequest) -> BatchRequest:
        requests.append(request)
        return prepare(request)

    def record_source(selection: DocumentSelectionRecord, observation: SourceObservation) -> None:
        writes.append(("source", observation.number, threading.get_ident()))
        save_source(selection, observation)

    def record_intent(intent: BatchIntent) -> None:
        writes.append(("intent", intent.request.number, threading.get_ident()))
        save_intent(intent)

    monkeypatch.setattr(current.batch, "prepare_request", counted)
    monkeypatch.setattr(current.repo, "save_source_observation", record_source)
    monkeypatch.setattr(current.repo, "save_batch_intent", record_intent)
    current.run(track=False)

    assert len(requests) == len(current.batch.submitted) == 3
    assert writes == [
        (kind, number, caller)
        for number in ("4100", "4101", "4102")
        for kind in ("source", "intent")
    ]
    batches = current.repo.list_open_llm_batches()
    assert len(batches) == 1
    items = current.repo.list_llm_batch_items(batches[0].batch_id)
    for item in items:
        assert isinstance(item.meta, BatchItemMeta), (
            "the actual primary caller stores primary metadata"
        )
        receipt = item.meta.prepared_input_receipt
        assert isinstance(receipt, PrimaryInputReceipt), (
            "native loose Word input has a corroborated single-part receipt"
        )
        companion = receipt.block_companion
        assert companion is not None, "the same-pass Word run intersects the budget prefix"
        (copy,) = companion.rows
        assert (copy.start, copy.end, copy.intersection) == (0, 1000, "clipped-v1")
        assert receipt.text_chars < receipt.source_part.chars
        assert receipt.request_sha256 == item.meta.request_sha256
        assert (
            receipt.source_selection_id is not None
            and receipt.artifact_sha256 is not None
            and receipt.extraction_id is not None
        )
        selection = current.repo.get_document_selection(receipt.source_selection_id)
        assert selection is not None and not any(
            span.stage == "analysis" for span in selection.selection.coverage
        )
        assert current.repo.get_artifact_metadata(receipt.artifact_sha256) is not None
        extraction = current.repo.get_extraction_result(receipt.extraction_id)
        assert extraction is not None and extraction.artifact_sha256 == receipt.artifact_sha256
    current.repo.restore(current.repo.dump())
    assert current.repo.list_llm_batch_items(batches[0].batch_id) == items
    current.run(track=False)
    assert len(requests) == len(current.batch.submitted) == 3
    assert current.repo.list_llm_batch_items(batches[0].batch_id) == items


@pytest.mark.parametrize("stage", ["capture", "enrichment"])
@pytest.mark.parametrize("broken_log", [False, True])
def test_new_metadata_failure_is_fail_open_without_private_diagnostics(
    stage: str,
    broken_log: bool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    current = world(docx(TEXT))
    hook = "capture_primary_input_receipt" if stage == "capture" else "enrich_primary_input_intent"

    def broken_warning(message: object, *args: object, **kwargs: object) -> None:
        raise RuntimeError("PRIVATE logging error")

    with patch.object(module, hook, side_effect=ValueError("PRIVATE metadata error")):
        if broken_log:
            monkeypatch.setattr(module.log, "warning", broken_warning)
        current.run(track=False)
    batch = current.repo.list_open_llm_batches()[0]
    item = current.repo.list_llm_batch_items(batch.batch_id)[0]
    assert isinstance(item.meta, BatchItemMeta), (
        "metadata failures preserve the original primary intent"
    )
    assert item.meta.prepared_input_receipt is None and len(current.batch.submitted) == 1
    assert current.bill("4100").status is BillStatus.BATCH_PENDING
    assert current.repo.list_source_observations(10, "4100")
    assert "PRIVATE" not in caplog.text and "Traceback" not in caplog.text


def test_receipts_preserve_original_provider_payload_cost_and_keys() -> None:
    data = docx(TEXT)
    baseline, candidate = world(data), world(data)
    with patch.object(module, "capture_primary_input_receipt", return_value=None):
        baseline.run(track=False)
    candidate.run(track=False)
    assert candidate.batch.submitted == baseline.batch.submitted
    assert candidate.bill("4100") == baseline.bill("4100")
    old_batch = baseline.repo.list_open_llm_batches()[0]
    new_batch = candidate.repo.list_open_llm_batches()[0]
    old_item = baseline.repo.list_llm_batch_items(old_batch.batch_id)[0]
    new_item = candidate.repo.list_llm_batch_items(new_batch.batch_id)[0]
    assert isinstance(new_item.meta, BatchItemMeta), "the primary caller preserves primary metadata"
    assert (
        new_item.model_copy(
            update={"meta": new_item.meta.model_copy(update={"prepared_input_receipt": None})}
        )
        == old_item
    )


def test_packaged_source_does_not_activate_the_loose_text_profile() -> None:
    current = world(archive({"actual.docx": docx(TEXT)}))
    current.run(track=False)
    batch = current.repo.list_open_llm_batches()[0]
    item = current.repo.list_llm_batch_items(batch.batch_id)[0]
    assert isinstance(item.meta, BatchItemMeta), (
        "the original package still queues its primary input"
    )
    assert item.meta.selection.parts[0].members
    assert item.meta.prepared_input_receipt is None


def test_actual_unsafe_graph_keeps_only_independent_body_and_extraction_refs() -> None:
    current = world(docx(TEXT))
    info = current.gateway.prints["4100"]
    url = print_url("4100") + "?token=PRIVATE_SOURCE"
    current.gateway.prints["4100"] = info.model_copy(
        update={"attachments": (info.attachments[0].model_copy(update={"url": url}),)}
    )
    current.gateway.files[url] = current.gateway.files[print_url("4100")]
    current.run(track=False)
    batch = current.repo.list_open_llm_batches()[0]
    item = current.repo.list_llm_batch_items(batch.batch_id)[0]
    assert isinstance(item.meta, BatchItemMeta), (
        "the original unsafe URL still supplies primary text"
    )
    receipt = item.meta.prepared_input_receipt
    assert isinstance(receipt, PrimaryInputReceipt), (
        "physical-only capture preserves the actual independent known parents"
    )
    assert receipt.source_selection_id is None
    assert receipt.artifact_sha256 is not None and receipt.extraction_id is not None
    assert current.repo.get_artifact_metadata(receipt.artifact_sha256) is not None
    assert current.repo.get_extraction_result(receipt.extraction_id) is not None
    assert not current.repo.list_source_observations(10, "4100")
    assert "PRIVATE_SOURCE" not in receipt.model_dump_json()


@pytest.mark.parametrize("stage", ["source", "backend", "sql"])
def test_original_source_backend_and_sql_failure_objects_still_propagate(
    stage: Literal["source", "backend", "sql"],
) -> None:
    current = world(docx(TEXT))
    current.repo.upsert_summary(current.gateway.details["4100"], now=current.clock.now())
    current.repo.set_status(10, "4100", BillStatus.ANALYSIS_PENDING)
    failure = (
        ServiceUnavailableError("original source failure")
        if stage == "source"
        else RuntimeError("original business failure")
    )
    owner, method = (
        (current.container.text_loader(), "read")
        if stage == "source"
        else (current.batch, "prepare_request")
        if stage == "backend"
        else (current.repo, "save_batch_intent")
    )
    with patch.object(owner, method, side_effect=failure), pytest.raises(type(failure)) as raised:
        current.analysis.analyze_bill(current.bill("4100"))
    assert raised.value is failure
    assert not current.repo.list_queued_batch_intents() and not current.batch.submitted


@pytest.mark.parametrize("stage", ["capture", "enrichment"])
def test_new_metadata_cancellation_propagates_and_never_queues_or_submits(stage: str) -> None:
    current = world(docx(TEXT))
    current.repo.upsert_summary(current.gateway.details["4100"], now=current.clock.now())
    current.repo.set_status(10, "4100", BillStatus.ANALYSIS_PENDING)
    failure = KeyboardInterrupt("controlled metadata cancellation")
    cause = OSError("controlled original cause")
    failure.__cause__ = cause
    hook = "capture_primary_input_receipt" if stage == "capture" else "enrich_primary_input_intent"
    with (
        patch.object(module, hook, side_effect=failure),
        pytest.raises(KeyboardInterrupt) as caught,
    ):
        current.analysis.analyze_bill(current.bill("4100"))
    assert caught.value is failure and caught.value.__cause__ is cause
    assert not current.repo.list_queued_batch_intents() and not current.batch.submitted
    assert current.bill("4100").status is BillStatus.ANALYSIS_PENDING


def test_native_doc_note_metadata_preserves_paid_request_memo_and_same_byte_alias() -> None:
    prefix = "USTAWA\r" + "Art. 1. Cudzoziemiec otrzymuje zezwolenie i sklada wniosek.\r" * 8
    data = ole_document(
        *source_notes(main=prefix + "\x02\r", reference_cp=len(prefix.encode("utf-16-le")) // 2)
    )
    baseline, candidate = world(data), world(data)
    with patch.object(
        document_module, "summarize_doc_note_projections", side_effect=lambda result, linked: result
    ):
        baseline.run(track=False)
    candidate.run(track=False)

    assert len(baseline.batch.submitted) == len(candidate.batch.submitted) == 1
    assert candidate.batch.submitted == baseline.batch.submitted
    old_context, new_context = baseline.batch.submitted[0].ctx, candidate.batch.submitted[0].ctx
    assert isinstance(old_context, BillContext) and isinstance(new_context, BillContext), (
        "The initial primary analysis prepares the bill context, not a secondary purpose"
    )
    assert build_user_prompt(new_context) == build_user_prompt(old_context)
    items = [
        current.repo.list_llm_batch_items(current.repo.list_open_llm_batches()[0].batch_id)[0]
        for current in (baseline, candidate)
    ]
    old_meta, new_meta = items[0].meta, items[1].meta
    assert isinstance(old_meta, BatchItemMeta) and isinstance(new_meta, BatchItemMeta), (
        "The native primary source queues the standard primary batch metadata"
    )
    assert old_meta.memo_key == new_meta.memo_key
    old_receipt, new_receipt = old_meta.prepared_input_receipt, new_meta.prepared_input_receipt
    assert isinstance(old_receipt, PrimaryInputReceipt) and isinstance(
        new_receipt, PrimaryInputReceipt
    ), "The complete native leaf records independently persisted physical input receipts"
    assert old_receipt.extraction_id != new_receipt.extraction_id
    for current, receipt, expected in ((baseline, old_receipt, 0), (candidate, new_receipt, 1)):
        assert receipt.extraction_id is not None, "The native source receipt records its extraction"
        result = current.repo.get_extraction_result(receipt.extraction_id)
        assert result is not None, "The caller commits the extraction before its input receipt"
        (notes,) = [part.doc_notes for part in result.selected_parts if part.doc_notes is not None]
        assert int(bool(notes.projections)) == expected
        current.batch.resolve()
        current.run(track=False)
        alias = TextDocument(url="https://api.test/alias.pdf", kind="print")
        current.gateway.files[alias.url] = data
        assert current.analysis.reanalyze_bill(current.bill("4100"), alias) is None
        assert len(current.batch.submitted) == 1


@pytest.mark.parametrize("missing_parent", [False, True])
def test_loose_rcl_receipt_is_saved_only_with_its_recorded_parents(
    missing_parent: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = World(extractor=reader(), batch_only=True, text_prefilter=False)
    main = rcl_document(1, "Projekt ustawy.docx")
    annex = rcl_document(2, "Załącznik do ustawy.docx")
    project = rcl_project(
        stages=(rcl_stage(1, "Projekt", "reached", rcl_folder(1, "Projekt", main, annex)),),
        consultation=None,
    )
    current.add_rcl_project(project)
    current.rcl.files.update(
        {
            main.url: docx(TEXT + "\nUZASADNIENIE\n" + "Treść uzasadnienia. " * 40),
            annex.url: docx(
                "Załącznik do ustawy\n" + "Wniosek składa się w terminie 30 dni. " * 30
            ),
        }
    )
    if missing_parent:

        def omit_link(reference: SelectionExtractionReference) -> None:
            pass

        monkeypatch.setattr(current.repo, "save_selection_extraction", omit_link)

    current.run(track=False)

    (batch,) = current.repo.list_open_llm_batches()
    (item,) = current.repo.list_llm_batch_items(batch.batch_id)
    receipt = item.meta.prepared_input_receipt
    if missing_parent:
        assert receipt is None
    else:
        assert isinstance(receipt, LooseRclInputReceipt), (
            "the exact loose bundle and every extracted part were corroborated"
        )
        assert len(receipt.source_parts) == 2
        assert receipt.request_sha256 == current.batch.submitted[0].payload_sha256
    assert current.bill(f"RCL/{project.id}").status is BillStatus.BATCH_PENDING
    dumped = current.repo.dump()
    current.repo.restore(dumped)
    assert current.repo.list_llm_batch_items(batch.batch_id) == [item]
    assert current.repo.dump() == dumped


def test_odt_row_receipt_survives_batch_cleanup_and_restore() -> None:
    raw = odt_document(
        "<text:p>" + "USTAWA Art. 1. Cudzoziemiec składa wniosek. " * 8 + "</text:p>"
        "<table:table><table:table-row>"
        "<table:table-cell><text:p>Termin</text:p></table:table-cell>"
        "<table:table-cell><text:p>31 grudnia</text:p></table:table-cell>"
        "</table:table-row></table:table>"
    )
    current = world(raw)

    current.run(track=False)

    (batch,) = current.repo.list_open_llm_batches()
    (item,) = current.repo.list_llm_batch_items(batch.batch_id)
    assert isinstance(item.meta, BatchItemMeta), "the actual primary caller saves primary metadata"
    receipt = item.meta.prepared_input_receipt
    assert isinstance(receipt, PrimaryInputReceipt), "the loose native ODT stores its primary input"
    companion = receipt.block_companion
    assert companion is not None, "the exact ODT row is within the prepared input"
    row = companion.rows[-1]
    extraction = current.repo.get_extraction_result(row.extraction_id)
    assert extraction is not None, "the copy is tied to a retained extraction"
    witness = extraction.selected_parts[0].block_copy_witness
    assert witness is not None, "the extraction retains its exact copies"
    assert witness.runs[row.run_index].block_id == "odt-xml:content.xml:body:1/0"
    assert item.meta.text[row.start : row.end] == "Termin\t31 grudnia"
    assert receipt.request_sha256 == current.batch.submitted[0].payload_sha256

    current.batch.resolve(batch.batch_id)
    current.run(track=False)
    current.batch.forget(batch.batch_id)
    current.repo.mark_llm_batch_forgotten(batch.batch_id, forgotten_at=current.clock.now())
    cutoff = current.clock.now() + timedelta(days=181)
    current.repo.prune_history(before=cutoff, memo_before=cutoff)
    sql = current.repo.dump()
    current.repo.restore(sql)
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(current.repo.dump())
        (row_json,) = connection.execute(
            "SELECT meta_json FROM llm_batch_items WHERE batch_id=?", (batch.batch_id,)
        ).fetchone()
    restored = BatchItemMeta.model_validate_json(row_json)
    assert restored.text == ""
    assert restored.prepared_input_receipt == receipt
    assert current.repo.get_extraction_result(row.extraction_id) == extraction
    assert current.repo.dump() == sql

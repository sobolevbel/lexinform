import threading
from typing import Literal
from unittest.mock import patch

import pytest

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    BatchIntent,
    BatchItemMeta,
    BatchRequest,
    BillStatus,
    DocumentSelectionRecord,
    SourceObservation,
)
from lexinform.services import analysis as module
from tests.harness import World, print_url
from tests.scenario.test_extraction_source_history import reader
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
        assert receipt is not None, "native loose Word input has a corroborated receipt"
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
    assert receipt is not None, (
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

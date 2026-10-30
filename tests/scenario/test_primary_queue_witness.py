import threading
from collections.abc import Sequence
from typing import Literal

import pytest

from lexinform.errors import BatchNotSubmittedError
from lexinform.models import (
    BatchIntent,
    BatchItemMeta,
    BatchRequest,
    DocumentSelectionRecord,
    RunMode,
    SourceObservation,
)
from tests.harness import World
from tests.scenario.test_primary_input_receipts import TEXT, world
from tests.scenario.test_request_provenance import retained_item
from tests.unit.test_physical_extraction import docx


def recorded_queue_boundaries(
    current: World, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[BatchRequest], list[BatchIntent], list[tuple[str, str]]]:
    caller = threading.get_ident()
    prepare = current.batch.prepare_request
    save_source = current.repo.save_source_observation
    save_intent = current.repo.save_batch_intent
    prepared: list[BatchRequest] = []
    intents: list[BatchIntent] = []
    writes: list[tuple[str, str]] = []
    observations: dict[str, SourceObservation] = {}

    def capture(request: BatchRequest) -> BatchRequest:
        result = prepare(request)
        prepared.append(result)
        return result

    def record_source(selection: DocumentSelectionRecord, observation: SourceObservation) -> None:
        assert threading.get_ident() == caller
        save_source(selection, observation)
        if observation.purpose == "analysis":
            observations[observation.number] = observation
            writes.append(("source", observation.number))
            current.clock.advance(seconds=1)

    def record_intent(intent: BatchIntent) -> None:
        assert threading.get_ident() == caller
        assert isinstance(intent.meta, BatchItemMeta), (
            "the native primary path creates primary meta"
        )
        stamp = intent.meta.queued_at
        assert stamp is not None, "new primary intent creation freezes its queue witness"
        source = observations[intent.request.number]
        assert source.recorded_at < stamp == intent.created_at == current.clock.now()
        assert current.repo.get_source_observation(source.observation_id) == source
        receipt = intent.meta.prepared_input_receipt
        assert receipt is not None, "native loose Word preparation retains its input receipt"
        assert receipt.source_selection_id == source.selection_id
        assert intent.request in prepared
        save_intent(intent)
        intents.append(intent)
        writes.append(("intent", intent.request.number))

    monkeypatch.setattr(current.batch, "prepare_request", capture)
    monkeypatch.setattr(current.repo, "save_source_observation", record_source)
    monkeypatch.setattr(current.repo, "save_batch_intent", record_intent)
    return prepared, intents, writes


@pytest.mark.parametrize("mode,workers", [("automatic", 1), ("automatic", 4), ("command", 1)])
def test_actual_primary_callers_finalize_source_before_the_original_queue_stamp(
    mode: Literal["automatic", "command"], workers: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    numbers = ("4100", "4101", "4102") if mode == "automatic" else ("4100",)
    current = world(docx(TEXT), workers=workers, numbers=numbers)
    prepared, intents, writes = recorded_queue_boundaries(current, monkeypatch)
    if mode == "automatic":
        current.run(track=False, max_publish=0)
    else:
        current.command("/analyze 4100")
        current.run(
            mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0
        )
    assert len(prepared) == len(intents) == len(current.batch.submitted) == len(numbers)
    assert writes == [(kind, number) for number in numbers for kind in ("source", "intent")]
    batch = current.repo.list_open_llm_batches()[0]
    items = current.repo.list_llm_batch_items(batch.batch_id)
    expected = {intent.request.custom_id: intent for intent in intents}
    for item in items:
        assert item.meta.model_dump(exclude={"request_sha256"}) == expected[
            item.custom_id
        ].meta.model_dump(exclude={"request_sha256"})
        assert item.meta.request_sha256 == expected[item.custom_id].request.payload_sha256
        assert item.meta.queued_at == expected[item.custom_id].created_at
    current.repo.restore(current.repo.dump())
    assert current.repo.list_llm_batch_items(batch.batch_id) == items


@pytest.mark.parametrize("legacy_unstamped", [False, True])
def test_submission_retry_copies_original_queue_metadata_without_preparing_again(
    legacy_unstamped: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = world(docx(TEXT))
    persist = current.repo.save_batch_intent
    prepared, intents, writes = recorded_queue_boundaries(current, monkeypatch)
    submit = current.batch.submit

    def rejected(requests: Sequence[BatchRequest]) -> str:
        raise BatchNotSubmittedError("injected rejection before remote acceptance")

    monkeypatch.setattr(current.batch, "submit", rejected)
    current.run(track=False, max_publish=0)
    queued = current.repo.list_queued_batch_intents()
    assert len(queued) == len(prepared) == len(intents) == 1
    original = queued[0]
    if legacy_unstamped:
        original = original.model_copy(
            update={"meta": original.meta.model_copy(update={"queued_at": None})}
        )
        current.repo.delete_batch_intents([original.request.custom_id])
        persist(original)
    assert original.meta.queued_at == (None if legacy_unstamped else original.created_at)
    before = original.model_dump_json()
    current.repo.restore(current.repo.dump())
    current.clock.advance(days=1)
    monkeypatch.setattr(current.batch, "submit", submit)
    current.analysis.start_run()
    current.analysis.submit_queued_batches()
    assert len(prepared) == 1 and writes == [("source", "4100"), ("intent", "4100")]
    assert current.batch.submitted == [original.request]
    batch = current.repo.list_open_llm_batches()[0]
    item = current.repo.list_llm_batch_items(batch.batch_id)[0]
    assert item.meta.model_dump(exclude={"request_sha256"}) == original.meta.model_dump(
        exclude={"request_sha256"}
    )
    assert original.model_dump_json() == before
    assert item.meta.request_sha256 == original.request.payload_sha256
    assert not current.repo.list_queued_batch_intents()
    job = current.repo.batch_job(original.request.custom_id)
    assert job is not None, "the submitted original intent has its durable batch job"
    assert job.since == (batch.submitted_at if legacy_unstamped else original.created_at)


def test_consumed_cleanup_retains_original_queue_witness_and_paid_result() -> None:
    current = world(docx(TEXT))
    current.run(track=False)
    batch = current.repo.list_open_llm_batches()[0]
    item = current.repo.list_llm_batch_items(batch.batch_id)[0]
    assert isinstance(item.meta, BatchItemMeta), "the native primary request has typed metadata"
    stamp, receipt = item.meta.queued_at, item.meta.prepared_input_receipt
    assert stamp is not None and receipt is not None, "new primary input retains both witnesses"
    current.repo.restore(current.repo.dump())
    current.batch.resolve()
    current.run(track=False)
    record = current.bill("4100").analysis
    assert record is not None and record.computation_id is not None, (
        "the fake paid answer is retained and applied"
    )
    paid = current.repo.get_computation_result(record.computation_id)
    assert paid is not None, "the actual collection retains its immutable paid result"
    current.run(track=False)
    current.clock.advance(days=200)
    current.run(track=False)
    meta, result = retained_item(current, batch.batch_id)
    retained = BatchItemMeta.model_validate(meta)
    assert retained.queued_at == stamp and retained.prepared_input_receipt == receipt
    assert retained.request_sha256 == record.request_sha256
    assert "answer" not in result and "analysis" not in result
    assert current.repo.get_computation_result(record.computation_id) == paid
    current.repo.restore(current.repo.dump())
    restored_meta, _ = retained_item(current, batch.batch_id)
    assert BatchItemMeta.model_validate(restored_meta).queued_at == stamp

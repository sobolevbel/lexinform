import pytest
from pydantic import ValidationError

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    BatchIntent,
    BatchItemMeta,
    BatchResult,
    BillStatus,
    ComputationInputBinding,
    ComputationResult,
    DigestItemMeta,
    DocumentReviewRecord,
    ReviewContext,
    computation_id,
)
from lexinform.models.batch import BatchKind
from lexinform.models.reviews import ReviewPurpose
from tests.computations import KINDS, retained_result
from tests.harness import RCL, World, summary
from tests.scenario.test_review_computation_history import PURPOSES, queued_review, submit_review


@pytest.mark.parametrize("kind", KINDS)
def test_reader_keeps_known_input_binding_separate_from_collection_coordinates(
    kind: BatchKind,
) -> None:
    original = retained_result(kind)
    key = (
        original.record.context.memo_key(10)
        if isinstance(original.record, DocumentReviewRecord)
        else "original-question"
    )
    binding = ComputationInputBinding(
        term=10,
        number=RCL if kind in ("analysis", "reanalysis") else original.number,
        reuse_key=key,
    )
    result = ComputationResult.model_validate_json(
        original.model_copy(update={"input_binding": binding}).model_dump_json()
    )
    repository = SqliteBillRepository(":memory:")
    repository.migrate()
    repository.save_computation_result(result)
    repository.restore(repository.dump())

    loaded = repository.get_computation_result(result.result_id)

    assert loaded is not None, "the repository retains the explicitly saved future result"
    assert loaded == result and loaded.input_binding == binding
    assert loaded.number == original.number
    assert original.input_binding is None and "input_binding" not in original.model_dump()
    repository.close()


@pytest.mark.parametrize("purpose", PURPOSES)
def test_full_review_binding_keeps_its_recorded_key_without_recomputing_policy(
    purpose: ReviewPurpose,
) -> None:
    original = retained_result(purpose)
    binding = ComputationInputBinding(
        term=10, number="3039", reuse_key="recorded-historical-policy-key"
    )

    loaded = ComputationResult.model_validate_json(
        original.model_copy(update={"input_binding": binding}).model_dump_json()
    )

    assert loaded.input_binding == binding


@pytest.mark.parametrize("primary", [False, True])
def test_metadata_rejects_a_binding_with_a_different_frozen_question_key(primary: bool) -> None:
    metadata: BatchItemMeta | DigestItemMeta = (
        BatchItemMeta(
            input_chars=1,
            truncated=False,
            text_source="pdf",
            source_kind="print",
            source_url=None,
            revision=1,
            text_sha256=None,
            memo_key="original",
        )
        if primary
        else DigestItemMeta(memo_key="original")
    )
    binding = ComputationInputBinding(term=10, number="3039", reuse_key="different")

    with pytest.raises(ValidationError, match="frozen memo key"):
        type(metadata).model_validate(
            {**metadata.model_dump(), "input_binding": binding.model_dump()}
        )

    assert "input_binding" not in metadata.model_dump()


@pytest.mark.parametrize("ready", [False, True])
@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
def test_collector_bridge_keeps_future_primary_binding_through_handoff(
    ready: bool, workers: int, model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(profile="production", workers=workers, analysis_model=model)
    save = world.repo.save_batch_intent

    def future_intent(intent: BatchIntent) -> None:
        assert intent.meta.memo_key is not None, "the future writer freezes a known question key"
        binding = ComputationInputBinding(
            term=intent.request.term,
            number=intent.request.number,
            reuse_key=intent.meta.memo_key,
        )
        save(
            intent.model_copy(
                update={"meta": intent.meta.model_copy(update={"input_binding": binding})}
            )
        )

    monkeypatch.setattr(world.repo, "save_batch_intent", future_intent)
    project = world.add_rcl_project()
    world.run()
    request = world.batch.submitted[0]
    batch = world.repo.list_open_llm_batches()[0]
    if ready:
        world.batch.resolve()
        world.analysis.collect_batches()
    status = world.bill(RCL).status
    world.repo.upsert_summary(summary("5", project.title), now=world.clock.now())
    world.repo.set_status(10, "5", status)

    with world.repo.atomic():
        world.repo.move_primary_analysis_work(10, RCL, "5")
        world.repo.link_bills(10, RCL, "5")
    world.repo.restore(world.repo.dump())
    if not ready:
        world.batch.resolve()
        world.analysis.collect_batches()

    paid = world.repo.get_computation_result(
        computation_id(batch.provider, batch.batch_id, request.custom_id)
    )
    assert paid is not None and paid.input_binding is not None, (
        "the bridge retains the known binding seeded by the future intent writer"
    )
    assert (paid.input_binding.term, paid.input_binding.number) == (10, RCL)
    assert paid.number == (RCL if ready else "5")
    assert paid.request_sha256 == request.payload_sha256
    assert world.bill("5").ready_analysis is not None
    assert world.bill(RCL).status is BillStatus.LINKED
    assert world.batch.submitted == [request]


@pytest.mark.parametrize("purpose", PURPOSES)
@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("changed_policy", [False, True])
@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
def test_collector_bridge_keeps_future_review_binding_for_answers_and_failures(
    purpose: ReviewPurpose,
    failed: bool,
    changed_policy: bool,
    model: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, source, intent = queued_review(purpose, model=model)
    assert isinstance(intent.meta, DigestItemMeta), "the fixture queues a document review"
    binding = ComputationInputBinding(term=10, number="3039", reuse_key=intent.meta.memo_key)
    intent = intent.model_copy(
        update={"meta": intent.meta.model_copy(update={"input_binding": binding})}
    )
    world.repo.delete_batch_intents([intent.request.custom_id])
    world.repo.save_batch_intent(intent)
    submit_review(world, intent)
    world.repo.restore(world.repo.dump())
    world.batch.resolve()
    original_key = source.memo_key(10)

    def changed_key(context: ReviewContext, term: int) -> str:
        return "document-review:" + "b" * 64

    if changed_policy:
        monkeypatch.setattr(ReviewContext, "memo_key", changed_key)

    def invalid(batch_id: str, kind: str = "analysis") -> list[BatchResult]:
        return [
            BatchResult(custom_id=intent.request.custom_id, error="invalid answer", input_tokens=19)
        ]

    if failed:
        monkeypatch.setattr(world.batch, "fetch_results", invalid)
    world.analysis.collect_batches()
    world.repo.restore(world.repo.dump())

    paid = world.repo.list_computation_results(10, "3039")[0]
    assert paid.input_binding == binding
    assert paid.review_input is not None, "the frozen fixture context supplies an input reference"
    assert paid.review_input.key == binding.reuse_key == original_key
    assert (paid.record is None) == failed
    assert paid.request_sha256 == intent.request.payload_sha256
    raw = paid.model_dump()
    raw["input_binding"] = binding.model_copy(update={"number": "3040"}).model_dump()
    with pytest.raises(ValidationError, match="frozen input binding"):
        ComputationResult.model_validate(raw)

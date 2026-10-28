import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest

from lexinform.errors import LlmUnavailableError
from lexinform.models import BatchIntent, BillStatus, DigestItemMeta, TextDocument
from lexinform.models.batch import batch_item_meta
from lexinform.models.reviews import ReviewPurpose
from lexinform.sections import TextBudget
from lexinform.services.analysis import AnalysisService, Waiting
from tests.fakes import FakeTextExtractor
from tests.harness import World
from tests.scenario.test_review_computation_history import PURPOSES, queued_review, submit_review
from tests.scenario.test_secondary_computation_history import KINDS, Kind, ask, prepared_world


@pytest.mark.parametrize("stale", [False, True])
@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
def test_primary_writer_pins_input_before_submission_and_keeps_it_after_reset(
    stale: bool, workers: int, model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(profile="production", workers=workers, analysis_model=model)
    captured: list[BatchIntent] = []
    save = world.repo.save_batch_intent

    def save_intent(intent: BatchIntent) -> None:
        binding = intent.meta.input_binding
        assert binding is not None, "the new intent factory binds input before repository IO"
        assert (binding.term, binding.number, binding.reuse_key) == (
            intent.request.term,
            intent.request.number,
            intent.meta.memo_key,
        )
        captured.append(intent)
        save(intent)

    monkeypatch.setattr(world.repo, "save_batch_intent", save_intent)
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    assert len(captured) == 1
    request = world.batch.submitted[0]
    assert request == captured[0].request
    if stale:
        world.repo.reset_bill(10, "3039", BillStatus.SKIPPED_PREFILTER)
    world.repo.restore(world.repo.dump())
    world.batch.resolve()

    world.analysis.collect_batches()

    paid = world.repo.list_computation_results(10, "3039")[0]
    assert paid.input_binding == captured[0].meta.input_binding
    assert paid.request_sha256 == request.payload_sha256
    assert paid.generation == 0
    applications = world.repo.list_result_applications(10, "3039")
    assert len(applications) == 1 and applications[0].outcome == (
        "superseded" if stale else "ready"
    )
    assert world.batch.submitted == [request]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("workers", [1, 4])
def test_secondary_writer_and_cleanup_preserve_known_or_unknown_original_binding(
    kind: Kind, legacy: bool, workers: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = prepared_world(workers=workers)
    captured: list[BatchIntent] = []
    save = world.repo.save_batch_intent

    def save_intent(intent: BatchIntent) -> None:
        assert intent.meta.input_binding is not None, "new secondary work uses the bound factory"
        if legacy:
            intent = intent.model_copy(
                update={"meta": intent.meta.model_copy(update={"input_binding": None})}
            )
        captured.append(intent)
        save(intent)

    monkeypatch.setattr(world.repo, "save_batch_intent", save_intent)
    assert isinstance(ask(world, kind), Waiting)
    assert len(captured) == 1
    original = captured[0]
    world.analysis.submit_queued_batches()
    world.repo.restore(world.repo.dump())
    world.batch.resolve()

    world.analysis.collect_batches()
    world.analysis.acknowledge_batch_costs()
    paid = next(
        row for row in world.repo.list_computation_results(10, "3039") if row.purpose == kind
    )
    assert paid.input_binding == original.meta.input_binding
    assert (paid.input_binding is None) == legacy
    assert paid.request_sha256 == original.request.payload_sha256
    world.repo.restore(world.repo.dump())
    world.analysis.start_run()
    world.analysis.collect_batches()
    assert not world.repo.list_unforgotten_llm_batches()
    world.clock.advance(days=200)
    world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
    world.repo.restore(world.repo.dump())

    assert world.repo.get_computation_result(paid.result_id) == paid
    with closing(sqlite3.connect(":memory:")) as database:
        database.executescript(world.repo.dump())
        row = database.execute(
            "SELECT meta_json FROM llm_batch_items WHERE custom_id = ?",
            (original.request.custom_id,),
        ).fetchone()
    assert row is not None, "retention keeps the consumed batch identity and metadata ledger"
    batch_item_meta(kind, row[0])


@pytest.mark.parametrize("purpose", PURPOSES)
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("model", ["gpt-5.1", "claude-opus-5-5"])
def test_review_writer_collects_old_unbound_and_new_bound_work(
    purpose: ReviewPurpose, legacy: bool, workers: int, model: str
) -> None:
    world, context, intent = queued_review(purpose, model=model, workers=workers)
    assert isinstance(intent.meta, DigestItemMeta), "the review fixture queued digest metadata"
    binding = intent.meta.input_binding
    assert binding is not None, "new review intents record their original subject and context key"
    assert (binding.term, binding.number, binding.reuse_key) == (
        10,
        "3039",
        context.memo_key(10),
    )
    if legacy:
        intent = intent.model_copy(
            update={"meta": intent.meta.model_copy(update={"input_binding": None})}
        )
        world.repo.delete_batch_intents([intent.request.custom_id])
        world.repo.save_batch_intent(intent)
    submit_review(world, intent)
    world.repo.restore(world.repo.dump())
    world.batch.resolve()

    world.analysis.collect_batches()
    world.repo.restore(world.repo.dump())

    paid = world.repo.list_computation_results(10, "3039")[0]
    assert paid.input_binding == (None if legacy else binding)
    assert paid.request_sha256 == intent.request.payload_sha256
    assert ("input_binding" in paid.model_dump()) != legacy


@pytest.mark.parametrize("workers", [1, 4])
def test_reanalysis_writer_binds_the_new_question_without_replacing_the_baseline(
    workers: int,
) -> None:
    old_text = "Art. 1. Cudzoziemiec posiadający zezwolenie na pobyt otrzymuje 200 zł. " * 40
    new_text = old_text.replace("200 zł", "300 zł")
    world = World(
        profile="production",
        workers=workers,
        extractor=FakeTextExtractor(by_content={b"%PDF": old_text, b"%PDF-new": new_text}),
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    world.run(track=False)
    world.batch.resolve()
    world.run(track=False)
    baseline = world.bill("3039").analysis
    url = "https://api.test/reanalysis.pdf"
    world.gateway.files[url] = b"%PDF-new"
    world.analysis.start_run()

    result = world.analysis.reanalyze_bill(world.bill("3039"), TextDocument(url=url, kind="print"))

    assert result is None and world.bill("3039").analysis == baseline
    intent = world.repo.list_queued_batch_intents()[0]
    assert intent.request.call_kind == "reanalysis"
    binding = intent.meta.input_binding
    assert binding is not None, "new reanalysis work freezes its own input scope"
    assert (binding.term, binding.number, binding.reuse_key) == (10, "3039", intent.meta.memo_key)
    world.analysis.submit_queued_batches()
    world.repo.restore(world.repo.dump())
    world.batch.resolve()
    world.analysis.collect_batches()

    paid = next(
        row
        for row in world.repo.list_computation_results(10, "3039")
        if row.purpose == "reanalysis"
    )
    assert paid.input_binding == binding
    assert paid.request_sha256 == intent.request.payload_sha256
    assert world.bill("3039").analysis == baseline


def test_explanation_without_a_configured_provider_never_queues_guessed_ownership() -> None:
    world = prepared_world()
    options = replace(world.container.analysis_options(), batch_provider=None)
    analysis = AnalysisService(
        world.repo,
        world.container.text_sources(),
        world.container.text_loader(),
        world.llm,
        world.clock,
        options,
        text_budget=TextBudget(world.container.settings.text_budget_chars),
        batch=world.batch,
    )
    submitted = list(world.batch.submitted)

    with pytest.raises(LlmUnavailableError, match="provider is unavailable for explanation"):
        analysis.explain_bill(world.bill("3039"))

    assert not world.repo.list_queued_batch_intents()
    assert world.batch.submitted == submitted

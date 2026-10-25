import pytest

from lexinform.errors import LlmContextExceededError
from lexinform.models import BillContext
from lexinform.models.batch import BatchItemMeta
from lexinform.pricing import INPUT_TOKEN_LIMITS
from tests.fakes import FakeBatchBackend, FakeTextExtractor
from tests.harness import World
from tests.scenario.test_document_selection_contract import law


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("batch", [False, True])
def test_budget_coverage_reaches_the_result_and_frozen_batch_metadata(
    workers: int, batch: bool
) -> None:
    source = law() + "\n" + "x" * 6000 + " cudzoziemcy " + "z" * 4000
    world = World(
        extractor=FakeTextExtractor(source),
        workers=workers,
        batch_only=batch,
        text_budget_chars=1000,
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    world.run(track=False)

    if batch:
        ctx = world.batch.submitted[0].ctx
        assert isinstance(ctx, BillContext), "this run submits only the bill analysis"
        item = world.repo.list_llm_batch_items(world.repo.list_open_llm_batches()[0].batch_id)[0]
        assert isinstance(item.meta, BatchItemMeta), "the submitted job is a bill analysis"
        selection = item.meta.selection
        frozen = world.batch.submitted[0].payload_json
        assert frozen and "projection" not in frozen
    else:
        ctx = world.llm.contexts[0]
        record = world.bill("3039").analysis
        assert record is not None, "the fake returns a successful analysis"
        selection = record.selection
    ranges = [span for span in selection.coverage if span.stage == "analysis"]
    assert ranges and ranges[0].start == 0 and ranges[0].end > 0
    assert not any("input coverage unknown" in span.losses for span in ranges)
    assert sum(span.end - span.start for span in ranges) == len(ctx.text.replace("\n[...]\n", ""))
    assert not any(part.text for part in selection.parts)
    world.repo.restore(world.repo.dump())
    if batch:
        restored = world.repo.list_llm_batch_items(world.repo.list_open_llm_batches()[0].batch_id)[
            0
        ]
        assert isinstance(restored.meta, BatchItemMeta), "restore preserves the analysis job type"
        assert restored.meta.selection == selection
    else:
        saved = world.bill("3039").analysis
        assert saved is not None, "restore preserves the successful analysis"
        assert saved.selection == selection


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("batch", [False, True])
def test_additional_cost_fitting_retains_the_exact_input_ranges(workers: int, batch: bool) -> None:
    source = law() + "\n" + "x" * 200_000 + " cudzoziemcy " + "z" * 100_000
    world = World(
        extractor=FakeTextExtractor(source),
        text_budget_chars=1_000_000,
        max_bill_cost_usd=0.30,
        workers=workers,
        batch_only=batch,
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    world.run(track=False)

    if batch:
        ctx = world.batch.submitted[0].ctx
        assert isinstance(ctx, BillContext), "this run submits only the bill analysis"
        item = world.repo.list_llm_batch_items(world.repo.list_open_llm_batches()[0].batch_id)[0]
        assert isinstance(item.meta, BatchItemMeta), "this is the queued analysis metadata"
        selection = item.meta.selection
    else:
        ctx = world.llm.contexts[0]
        record = world.bill("3039").analysis
        assert record is not None, "the reduced input fits the fake model's budget"
        selection = record.selection
    assert ctx.truncated and len(ctx.text) < len(source)
    ranges = [span for span in selection.coverage if span.stage == "analysis"]
    assert ranges and all(span.end > span.start for span in ranges)
    assert not any("input coverage unknown" in span.losses for span in ranges)
    assert "".join(source[span.start : span.end] for span in ranges) == ctx.text.replace(
        "\n[...]\n", ""
    )
    assert not any(part.text for part in selection.parts)


def test_second_token_ceiling_cut_composes_ranges_to_the_original_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(INPUT_TOKEN_LIMITS, "claude-opus-5", 110_000)
    source = law() + "\n" + "Cudzoziemiec składa wniosek w terminie 30 dni.\n" * 4400
    world = World(
        extractor=FakeTextExtractor(source), text_budget_chars=1_000_000, max_bill_cost_usd=0.5
    )
    world.llm.count_overshoot = 1.5
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    world.run(track=False)

    assert len(world.llm.counted) == 4
    record = world.bill("3039").analysis
    assert record is not None, "both cuts fit the input ceiling"
    ctx = world.llm.contexts[0]
    assert ctx.truncated and len(ctx.text) < 150_000
    ranges = [span for span in record.selection.coverage if span.stage == "analysis"]
    assert ranges and not any("input coverage unknown" in span.losses for span in ranges)
    assert "".join(source[span.start : span.end] for span in ranges) == ctx.text.replace(
        "\n[...]\n", ""
    )


@pytest.mark.parametrize("route", ["preflight", "synchronous_rejection", "batch_rejection"])
def test_overflow_cost_fitting_uses_the_original_input_and_its_ranges(
    route: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = law() + "\n" + "x" * 200_000 + " cudzoziemcy " + "z" * 100_000
    if route == "preflight":
        monkeypatch.setitem(INPUT_TOKEN_LIMITS, "gpt-5.1", 50_000)
    rejected = LlmContextExceededError("context_length_exceeded")
    overflow = FakeBatchBackend(id_prefix="overflow")
    world = World(
        extractor=FakeTextExtractor(source),
        text_budget_chars=1_000_000,
        analysis_model="claude-opus-5" if route == "synchronous_rejection" else "gpt-5.1",
        overflow_batch=overflow,
        batch_only=route != "synchronous_rejection",
        max_bill_cost_usd=0.10 if route == "batch_rejection" else 0.30,
        llm_script={"3039": rejected} if route == "synchronous_rejection" else None,
        batch_script={"3039": rejected} if route == "batch_rejection" else None,
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    world.run(track=False)
    if route == "batch_rejection":
        world.batch.resolve()
        world.analysis.collect_batches()
        world.repo.restore(world.repo.dump())
        world.run(track=False)

    (request,) = overflow.submitted
    ctx = request.ctx
    assert isinstance(ctx, BillContext), "the overflow request is a bill analysis"
    assert ctx.truncated and len(ctx.text) < len(source)
    if route == "synchronous_rejection":
        assert len(ctx.text) > len(world.llm.contexts[0].text)
    jobs = world.repo.list_open_llm_batches()
    item = next(
        item
        for job in jobs
        for item in world.repo.list_llm_batch_items(job.batch_id)
        if isinstance(item.meta, BatchItemMeta) and item.meta.overflow
    )
    assert isinstance(item.meta, BatchItemMeta), "the selected item is the overflow analysis"
    ranges = [span for span in item.meta.selection.coverage if span.stage == "analysis"]
    assert ranges and not any("input coverage unknown" in span.losses for span in ranges)
    assert "".join(source[span.start : span.end] for span in ranges) == ctx.text.replace(
        "\n[...]\n", ""
    )
    assert request.payload_json is not None, (
        "the fake freezes the provider context before submission"
    )
    assert "projection" not in request.payload_json

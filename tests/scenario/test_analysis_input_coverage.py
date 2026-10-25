import pytest

from lexinform.models import BillContext
from lexinform.models.batch import BatchItemMeta
from tests.fakes import FakeTextExtractor
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


def test_additional_cost_fitting_keeps_coverage_unknown_until_its_map_is_propagated() -> None:
    source = law() + "\n" + "x" * 200_000 + " cudzoziemcy " + "z" * 100_000
    world = World(
        extractor=FakeTextExtractor(source), text_budget_chars=1_000_000, max_bill_cost_usd=0.30
    )
    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    world.run(track=False)

    record = world.bill("3039").analysis
    assert record is not None, "the reduced input fits the fake model's budget"
    assert world.llm.contexts[0].truncated
    assert len(world.llm.contexts[0].text) < len(source)
    ranges = [span for span in record.selection.coverage if span.stage == "analysis"]
    assert ranges and all((span.start, span.end) == (0, 0) for span in ranges)
    assert all("input coverage unknown" in span.losses for span in ranges)

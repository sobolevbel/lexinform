import pytest

from lexinform.models import BillContext
from lexinform.models.batch import BatchItemMeta
from tests.fakes import FakeTextExtractor
from tests.harness import World, rcl_document, rcl_folder, rcl_project, rcl_stage
from tests.scenario.test_document_selection_contract import law


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("cost_fit", [False, True])
def test_self_amendments_keep_main_ranges_through_budget_and_cost_fitting(
    batch: bool,
    cost_fit: bool,
) -> None:
    baseline = rcl_document(1, "Projekt ustawy.docx")
    amendment = rcl_document(2, "Autopoprawka.docx")
    body = law() + (
        "\n" + "Cudzoziemiec składa wniosek w terminie 30 dni.\n" * 6000 if cost_fit else ""
    )
    operations = "AUTOPOPRAWKA\nW art. 1 wyrazy 30 dni zastępuje się wyrazami 60 dni.\n" * 8
    project = rcl_project(
        stages=(rcl_stage(1, "Projekt", "reached", rcl_folder(1, "Projekt", baseline, amendment)),),
        consultation=None,
    )
    world = World(
        extractor=FakeTextExtractor(
            page_count=0, by_content={b"law": body, b"proposal": operations}
        ),
        text_budget_chars=1_000_000,
        max_bill_cost_usd=0.30 if cost_fit else 0,
        batch_only=batch,
    )
    world.add_rcl_project(project)
    world.rcl.files.update({baseline.url: b"law", amendment.url: b"proposal"})

    world.run(track=False)

    if batch:
        ctx = world.batch.submitted[0].ctx
        assert isinstance(ctx, BillContext), "the queued job is a bill analysis"
        item = world.repo.list_llm_batch_items(world.repo.list_open_llm_batches()[0].batch_id)[0]
        assert isinstance(item.meta, BatchItemMeta), "the queued metadata belongs to the analysis"
        selection = item.meta.selection
    else:
        ctx = world.llm.contexts[0]
        record = world.bill(f"RCL/{project.id}").analysis
        assert record is not None, "the fake returns a successful analysis"
        selection = record.selection
    ranges = [span for span in selection.coverage if span.stage == "analysis"]
    assert ranges and all(span.end > span.start for span in ranges)
    assert not any("input coverage unknown" in span.losses for span in ranges)
    assert all(span.part_index == 0 and span.end <= len(body) for span in ranges)
    assert all(body[span.start : span.end] in ctx.text for span in ranges)
    assert any(part.role == "self_amendment" for part in selection.parts)
    if not cost_fit:
        assert operations in ctx.text and "nie tekst jednolity" in ctx.text
        assert [(span.start, span.end) for span in ranges] == [(0, len(body))]
    else:
        assert ctx.truncated

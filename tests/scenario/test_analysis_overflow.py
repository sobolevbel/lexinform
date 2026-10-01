from dataclasses import replace

import pytest

from lexinform.errors import LlmContextExceededError
from lexinform.models import BillContext, BillStatus, TextDocument
from lexinform.pricing import INPUT_TOKEN_LIMITS
from lexinform.services.analysis import Waiting
from tests.fakes import FakeBatchBackend, FakeTextExtractor
from tests.harness import World, print_url

LIMIT = 4_720
"""gpt-5.1's 272,000 a hundredfold smaller past the fake batch's 2,000 prompt tokens: 5k chars, not 540k."""


@pytest.fixture
def small_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same boundary over texts a hundred times shorter: the keyword scan is linear in them."""
    monkeypatch.setitem(INPUT_TOKEN_LIMITS, "gpt-5.1", LIMIT)


class OverflowBatch(FakeBatchBackend):
    MODEL = "claude-opus-5-5"

    def __init__(self, *, rejected: bool = False) -> None:
        super().__init__(
            {"3039": LlmContextExceededError("context_length_exceeded")} if rejected else None,
            id_prefix="opus",
        )


@pytest.mark.usefixtures("small_context")
@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("tokens", [LIMIT, LIMIT + 1])
def test_context_boundary_routes_full_text_to_correct_batch(workers: int, tokens: int) -> None:
    text = "a" * ((tokens - 2_000) * 2)
    overflow = OverflowBatch()
    w = World(
        analysis_model="gpt-5.1",
        overflow_batch=overflow,
        batch_only=True,
        workers=workers,
        extractor=FakeTextExtractor(text),
        text_budget_chars=1_500_000,
        max_bill_cost_usd=2,
    )
    for number in ("3039", "3040"):
        w.add_bill(number, "Projekt ustawy o cudzoziemcach")

    report = w.run()

    assert not report.errors and not w.llm.contexts
    selected = overflow if tokens > LIMIT else w.batch
    assert len(selected.submitted) == 2
    ctx = selected.submitted[0].ctx
    assert isinstance(ctx, BillContext)
    assert ctx.text == text
    assert not ctx.truncated
    assert w.bill("3039").status is BillStatus.BATCH_PENDING
    selected.resolve()
    w.run()
    record = w.bill("3039").analysis
    assert record is not None and record.model == selected.MODEL


@pytest.mark.usefixtures("small_context")
@pytest.mark.parametrize("force", [False, True])
def test_manual_overflow_uses_batch_price_and_force_only_lifts_cost_guard(force: bool) -> None:
    overflow = OverflowBatch()
    text = "a" * 120_000
    w = World(
        analysis_model="gpt-5.1",
        overflow_batch=overflow,
        batch_only=True,
        extractor=FakeTextExtractor(text),
        text_budget_chars=1_500_000,
        max_bill_cost_usd=0.1,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run(max_analyze=0)

    outcome = w.analysis.analyze_bill(w.bill("3039"), ignore_cost_limit=force)

    assert isinstance(outcome, Waiting)
    assert not w.llm.contexts and not w.batch.submitted
    ctx = overflow.submitted[0].ctx
    assert isinstance(ctx, BillContext)
    assert ctx.truncated != force
    assert ctx.text == text if force else len(ctx.text) / 2 * 2 / 1_000_000 <= 0.1
    assert isinstance(w.analysis.analyze_bill(w.bill("3039"), ignore_cost_limit=True), Waiting)
    assert len(overflow.submitted) == 1


@pytest.mark.parametrize(("pages", "limit"), [(170, 2), (171, 2), (313, 2), (313, 1)])
def test_scans_route_by_pages_and_batch_cost(pages: int, limit: float) -> None:
    overflow = OverflowBatch()
    w = World(
        analysis_model="gpt-5.1",
        overflow_batch=overflow,
        batch_only=True,
        extractor=FakeTextExtractor("", page_count=pages),
        max_bill_cost_usd=limit,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    report = w.run()

    assert not w.llm.contexts and not w.llm.counted
    assert bool(w.batch.submitted) == (pages == 170)
    assert bool(overflow.submitted) == (pages > 170 and limit == 2)
    assert report.analysis_skipped_cost == (1 if limit == 1 else 0)


def test_batch_context_rejection_survives_restart_and_switches_provider() -> None:
    overflow = OverflowBatch()
    w = World(
        analysis_model="gpt-5.1",
        overflow_batch=overflow,
        batch_only=True,
        batch_script={"3039": LlmContextExceededError("context_length_exceeded")},
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.batch.resolve()
    w.analysis.collect_batches()
    w.repo.restore(w.repo.dump())
    w.container = replace(w.container)
    w.pipeline = w.container.pipeline(dry_run=False)

    w.run()
    w.run()

    assert len(w.batch.submitted) == len(overflow.submitted) == 1
    assert w.batch.submitted[0].custom_id != overflow.submitted[0].custom_id
    assert not w.llm.contexts and w.bill("3039").analysis_attempts == 0
    overflow.resolve()
    report = w.run()
    assert w.bill("3039").analysis is not None
    assert any(call.model == overflow.MODEL and call.batched for call in report.llm_calls)


def test_synchronous_context_rejection_also_queues_overflow() -> None:
    overflow = OverflowBatch()
    w = World(
        analysis_model="gpt-5.1",
        overflow_batch=overflow,
        llm_script={"3039": LlmContextExceededError("context_length_exceeded")},
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run(max_analyze=0)

    outcome = w.analysis.analyze_bill(w.bill("3039"))

    assert isinstance(outcome, Waiting)
    assert len(w.llm.contexts) == len(overflow.submitted) == 1


@pytest.mark.usefixtures("small_context")
def test_overflow_context_rejection_is_not_submitted_again() -> None:
    overflow = OverflowBatch(rejected=True)
    w = World(
        analysis_model="gpt-5.1",
        overflow_batch=overflow,
        batch_only=True,
        extractor=FakeTextExtractor("a" * 6_000),
        text_budget_chars=1_500_000,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    overflow.resolve()

    w.run()
    w.run()

    assert len(overflow.submitted) == 1 and not w.llm.contexts


def test_reanalysis_context_rejection_uses_opus_batch() -> None:
    overflow = OverflowBatch()
    w = World(
        analysis_model="gpt-5.1",
        overflow_batch=overflow,
        batch_only=True,
        extractor=FakeTextExtractor(by_content={b"new": "Nowy tekst ustawy. " * 100}),
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.batch.resolve()
    w.run()
    w.batch.script["3039"] = LlmContextExceededError("context_length_exceeded")
    url = "https://api.test/new.pdf"
    w.gateway.files[url] = b"new"
    document = TextDocument(url=url, kind="print")
    assert w.analysis.reanalyze_bill(w.bill("3039"), document) is None
    w.analysis.submit_queued_batches()
    w.batch.resolve()
    w.analysis.collect_batches()

    assert w.analysis.reanalyze_bill(w.bill("3039"), document) is None
    w.analysis.submit_queued_batches()
    overflow.resolve()
    w.analysis.collect_batches()
    record = w.analysis.reanalyze_bill(w.bill("3039"), document)

    assert record is not None and record.model == overflow.MODEL and record.revision == 2
    assert len(overflow.submitted) == 1
    ctx = overflow.submitted[0].ctx
    assert isinstance(ctx, BillContext) and ctx.previous_summary is not None


def test_a_re_analysis_no_model_takes_keeps_the_previous_analysis_rather_than_failing() -> None:
    """The tracking loop reads a new text first: a raise there would stop the bill's updates."""
    overflow = OverflowBatch(rejected=True)
    w = World(
        analysis_model="gpt-5.1",
        overflow_batch=overflow,
        batch_only=True,
        extractor=FakeTextExtractor(by_content={b"new": "Nowy tekst ustawy. " * 100}),
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.batch.resolve()
    w.run()
    w.batch.script["3039"] = LlmContextExceededError("context_length_exceeded")
    url = "https://api.test/new.pdf"
    w.gateway.files[url] = b"new"
    document = TextDocument(url=url, kind="print")
    w.analysis.reanalyze_bill(w.bill("3039"), document)
    w.analysis.submit_queued_batches()
    w.batch.resolve()
    w.analysis.collect_batches()
    w.analysis.reanalyze_bill(w.bill("3039"), document)
    w.analysis.submit_queued_batches()
    overflow.resolve()
    w.analysis.collect_batches()

    again = w.analysis.reanalyze_bill(w.bill("3039"), document)

    assert again is None
    analysis = w.bill("3039").analysis
    assert analysis is not None and analysis.revision == 1
    assert len(overflow.submitted) == 1


@pytest.mark.usefixtures("small_context")
def test_openai_and_opus_batches_are_submitted_and_collected_in_one_run() -> None:
    overflow = OverflowBatch()
    w = World(
        analysis_model="gpt-5.1",
        overflow_batch=overflow,
        batch_only=True,
        extractor=FakeTextExtractor(by_content={b"large": "a" * 6_000}),
        text_budget_chars=1_500_000,
    )
    for number in ("3039", "3040"):
        w.add_bill(number, "Projekt ustawy o cudzoziemcach")
    w.gateway.files[print_url("3040")] = b"large"

    w.run()

    assert len(w.batch.submitted) == len(overflow.submitted) == 1
    assert {batch.provider for batch in w.repo.list_open_llm_batches()} == {"openai", "anthropic"}
    w.batch.resolve()
    overflow.resolve()
    report = w.run()
    assert report.analyzed == 2 and not report.errors
    assert not w.repo.list_open_llm_batches()

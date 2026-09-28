from dataclasses import replace

import pytest

from lexinform.errors import LlmContextExceededError, LlmUnavailableError
from lexinform.models import TextDocument
from tests.fakes import FakeLlm, FakeTextExtractor
from tests.harness import World


class OverflowLlm(FakeLlm):
    MODEL = "claude-opus-5-5"


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("tokens", [272_000, 272_001])
def test_context_boundary_routes_full_text_synchronously(
    batch: bool, workers: int, tokens: int
) -> None:
    text = "a" * ((tokens - (2_000 if batch else 0)) * 2)
    overflow = OverflowLlm()
    w = World(
        analysis_model="gpt-5.1",
        overflow_llm=overflow,
        batch=batch,
        workers=workers,
        extractor=FakeTextExtractor(text),
        text_budget_chars=1_500_000,
        max_bill_cost_usd=2,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o cudzoziemcach")

    report = w.run()

    assert not report.errors
    if tokens > 272_000:
        assert not w.llm.contexts and not w.batch.submitted
        assert len(overflow.contexts) == 2
        assert overflow.contexts[0].text == text
        assert not overflow.contexts[0].truncated
        record = w.bill("3039").analysis
        assert record is not None and record.model == overflow.MODEL
        assert any(call.model == overflow.MODEL for call in report.llm_calls)
    else:
        assert not overflow.contexts
        assert bool(w.batch.submitted) == batch
        assert bool(w.llm.contexts) != batch


@pytest.mark.parametrize("force", [False, True])
def test_overflow_uses_opus_cost_without_batch_discount(force: bool) -> None:
    text = "a" * 1_200_000
    overflow = OverflowLlm()
    w = World(
        analysis_model="gpt-5.1",
        overflow_llm=overflow,
        batch=True,
        extractor=FakeTextExtractor(text),
        text_budget_chars=1_500_000,
        max_bill_cost_usd=2,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    w.run(max_analyze=0)
    if force:
        w.analysis.analyze_bill(w.bill("3039"), ignore_cost_limit=True)
    else:
        w.run()

    ctx = overflow.contexts[0]
    assert not w.llm.contexts and not w.batch.submitted
    assert ctx.truncated != force
    if force:
        assert ctx.text == text
    else:
        assert len(ctx.text) / 2 * 4 / 1_000_000 <= 2


@pytest.mark.parametrize("pages", [170, 171, 313])
def test_scans_route_by_page_count_and_keep_opus_cost_guard(pages: int) -> None:
    overflow = OverflowLlm()
    w = World(
        analysis_model="gpt-5.1",
        overflow_llm=overflow,
        batch=True,
        extractor=FakeTextExtractor("", page_count=pages),
        max_bill_cost_usd=2,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    report = w.run()

    assert not w.llm.counted and not overflow.counted
    assert bool(w.batch.submitted) == (pages == 170)
    assert bool(overflow.contexts) == (pages == 171)
    assert report.analysis_skipped_cost == (1 if pages == 313 else 0)


def test_changed_text_reanalysis_uses_overflow_and_reuses_memo() -> None:
    overflow = OverflowLlm()
    w = World(
        analysis_model="gpt-5.1",
        overflow_llm=overflow,
        text_budget_chars=1_500_000,
        extractor=FakeTextExtractor(by_content={b"new": "a" * 600_000}),
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    bill = w.bill("3039")
    url = "https://api.test/new.pdf"
    w.gateway.files[url] = b"new"
    document = TextDocument(url=url, kind="print")

    record = w.analysis.reanalyze_bill(bill, document)
    reused = w.analysis.reanalyze_bill(bill, document)

    assert record is not None and record.model == overflow.MODEL
    assert reused is not None and reused.input_tokens == 0
    assert len(overflow.contexts) == 1
    assert overflow.contexts[0].previous_summary is not None


@pytest.mark.parametrize("force", [False, True])
def test_api_context_rejection_retries_on_opus(force: bool) -> None:
    overflow = OverflowLlm()
    w = World(
        analysis_model="gpt-5.1",
        overflow_llm=overflow,
        llm_script={"3039": LlmContextExceededError("context_length_exceeded")},
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run(max_analyze=0)

    outcome = w.analysis.analyze_bill(w.bill("3039"), ignore_cost_limit=force)

    assert outcome.record.model == overflow.MODEL
    assert len(w.llm.contexts) == len(overflow.contexts) == 1
    assert overflow.contexts[0].text == w.llm.contexts[0].text
    assert w.bill("3039").analysis_attempts == 0


def test_batch_context_rejection_survives_restore_and_never_resubmits() -> None:
    overflow = OverflowLlm()
    w = World(
        analysis_model="gpt-5.1",
        overflow_llm=overflow,
        batch=True,
        batch_script={"3039": LlmContextExceededError("context_length_exceeded")},
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.batch.resolve()
    w.analysis.collect_batches()
    w.repo.restore(w.repo.dump())
    w.container = replace(w.container)
    w.pipeline = w.container.pipeline(dry_run=False)

    report = w.run()
    w.run()

    assert not report.errors
    assert len(w.batch.submitted) == 1
    assert len(overflow.contexts) == 1 and not w.llm.contexts
    assert w.bill("3039").analysis_attempts == 0
    assert any(call.model == overflow.MODEL for call in report.llm_calls)


@pytest.mark.parametrize(
    "failure",
    [
        LlmUnavailableError("rate limited"),
        RuntimeError("invalid response"),
    ],
)
def test_unrelated_api_errors_do_not_use_overflow(failure: Exception) -> None:
    overflow = OverflowLlm()
    w = World(
        analysis_model="gpt-5.1",
        overflow_llm=overflow,
        llm_script={"3039": failure},
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    w.run()

    assert len(w.llm.contexts) == 1 and not overflow.contexts


def test_overflow_context_rejection_is_not_retried_again() -> None:
    overflow = OverflowLlm(script={"3039": LlmContextExceededError("still too large")})
    w = World(
        analysis_model="gpt-5.1",
        overflow_llm=overflow,
        llm_script={"3039": LlmContextExceededError("context_length_exceeded")},
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    w.run()

    assert len(w.llm.contexts) == len(overflow.contexts) == 1
    assert w.bill("3039").analysis_attempts == 1


def test_batch_reanalysis_context_rejection_uses_opus() -> None:
    overflow = OverflowLlm()
    w = World(
        analysis_model="gpt-5.1",
        overflow_llm=overflow,
        batch=True,
        batch_kinds=frozenset({"reanalysis"}),
        batch_script={"3039": LlmContextExceededError("context_length_exceeded")},
        extractor=FakeTextExtractor(by_content={b"new": "Nowy tekst ustawy. " * 100}),
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    url = "https://api.test/new.pdf"
    w.gateway.files[url] = b"new"
    document = TextDocument(url=url, kind="print")
    assert w.analysis.reanalyze_bill(w.bill("3039"), document) is None
    w.analysis.submit_queued_batches()
    w.batch.resolve()
    w.analysis.collect_batches()

    record = w.analysis.reanalyze_bill(w.bill("3039"), document)

    assert record is not None and record.model == overflow.MODEL and record.revision == 2
    assert len(w.batch.submitted) == len(overflow.contexts) == 1
    assert overflow.contexts[0].previous_summary is not None


@pytest.mark.parametrize("batch", [False, True])
def test_api_fallback_rechecks_cost_at_opus_price(batch: bool) -> None:
    error = LlmContextExceededError("context_length_exceeded")
    overflow = OverflowLlm()
    text = "a" * 500_000
    w = World(
        analysis_model="gpt-5.1",
        overflow_llm=overflow,
        batch=batch,
        llm_script={"3039": error},
        batch_script={"3039": error},
        extractor=FakeTextExtractor(text),
        text_budget_chars=1_500_000,
        max_bill_cost_usd=0.5,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    if batch:
        w.batch.resolve()
        w.run()

    ctx = overflow.contexts[0]
    assert ctx.truncated and len(ctx.text) / 2 * 4 / 1_000_000 <= 0.5

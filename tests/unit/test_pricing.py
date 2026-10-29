import pytest

from lexinform.models import LlmCall, TokenUsage
from lexinform.pricing import (
    batch_reservation,
    cost_usd,
    estimate_batch_reservation,
    input_limit_of,
    long_input_tier,
    price_of,
    scan_page_tokens,
    tiered,
)


def test_prices_match_by_family_prefix() -> None:
    assert price_of("claude-opus-5") == (5.0, 25.0)
    assert price_of("claude-sonnet-5-20260601") == (2.0, 10.0)
    assert price_of("claude-haiku-4-5") == (1.0, 5.0)
    assert price_of("fake") is None


def test_new_batch_reservations_do_not_charge_for_cache_writes() -> None:
    assert batch_reservation("claude-opus-5-5", input_tokens=1_000_000, max_output_tokens=0) == 2
    assert batch_reservation("gpt-5.1", input_tokens=1_000_000, max_output_tokens=0) == 0.625


@pytest.mark.parametrize("model", ["claude-opus-5-5", "gpt-5.1"])
@pytest.mark.parametrize(
    ("tokens", "reserved"), [(None, 2300), (0, 2300), (10, 2300), (3000, 3000)]
)
@pytest.mark.parametrize("pages", [0, 2])
@pytest.mark.parametrize("output", [0, 4000])
def test_batch_estimate_keeps_headroom_and_scan_allowance(
    model: str, tokens: int | None, reserved: int, pages: int, output: int
) -> None:
    assert estimate_batch_reservation(
        model,
        counted_tokens=tokens,
        prompt_chars=200,
        system_chars=100,
        scan_pages=pages,
        max_output_tokens=output,
    ) == batch_reservation(model, input_tokens=reserved + pages * 1600, max_output_tokens=output)


def test_cost_adds_models_and_cache_categories() -> None:
    usage = {
        # 287 711 uncached input + 334 output on Opus: the real druk 2695 analysis
        "claude-opus-5": TokenUsage(input=287_711, output=334),
        "claude-sonnet-5": TokenUsage(
            input=5_000, output=100, cache_read=1_000, cache_creation=500
        ),
    }
    cost = cost_usd(usage)
    assert cost is not None, "both models are in the price table"
    opus = 287_711 * 5 / 1e6 + 334 * 25 / 1e6
    sonnet = (5_000 * 2 + 1_000 * 2 * 0.1 + 500 * 2 * 1.25 + 100 * 10) / 1e6
    assert abs(cost - (opus + sonnet)) < 1e-9
    assert cost_usd({"claude-opus-5": TokenUsage(), "fake": TokenUsage(input=1)}) is None
    assert cost_usd({}) == 0.0


def test_sol_reservation_doubles_input_and_raises_output_above_272k() -> None:
    assert batch_reservation("gpt-6.1-sol", input_tokens=272_000, max_output_tokens=0) == 0.272
    long = batch_reservation("gpt-6.1-sol", input_tokens=600_000, max_output_tokens=8000)
    assert abs(long - (600_000 * 2.0 + 8000 * 7.5) / 1e6) < 1e-9
    assert input_limit_of("gpt-6.1-sol") == 922_000


def test_sol_reserves_its_own_scanned_page_tokens() -> None:
    reserved = estimate_batch_reservation(
        "gpt-6.1-sol",
        counted_tokens=None,
        prompt_chars=200,
        system_chars=100,
        scan_pages=13,
        max_output_tokens=0,
    )
    assert reserved == batch_reservation(
        "gpt-6.1-sol", input_tokens=2300 + 13 * 3110, max_output_tokens=0
    )


def test_sol_cache_reads_cost_a_twentieth_of_input() -> None:
    cost = cost_usd({"gpt-6.1-sol": TokenUsage(cache_read=1_000_000, cache_creation=1_000_000)})
    assert cost is not None and abs(cost - (0.05 * 2 + 1.25 * 2)) < 1e-9


def test_a_long_sol_call_is_summed_and_priced_at_its_long_tier() -> None:
    short = LlmCall(
        number="1", kind="analysis", model="gpt-6.1-sol", input_tokens=200_000, batched=True
    )
    long = LlmCall(
        number="2",
        kind="analysis",
        model="gpt-6.1-sol",
        input_tokens=583_000,
        cache_read_input_tokens=1_000,
        output_tokens=600,
        batched=True,
    )

    assert list(short.usage) == ["gpt-6.1-sol"] and list(long.usage) == ["gpt-6.1-sol:long"]
    cost = cost_usd(long.usage)
    assert cost is not None
    expected = (583_000 * 4.0 + 1_000 * 4.0 * 0.05 + 600 * 15.0) / 2 / 1e6
    assert abs(cost - expected) < 1e-9


def test_scan_and_long_tier_helpers_follow_the_model() -> None:
    assert scan_page_tokens("gpt-6.1-sol") == 3110 and scan_page_tokens("gpt-5.1") == 1600
    assert long_input_tier("gpt-6.1-sol") == (272_000, 2.0) and long_input_tier("gpt-5.1") is None
    assert (
        tiered(1.0, 300_000, (272_000, 2.0)) == 2.0 and tiered(1.0, 272_000, (272_000, 2.0)) == 1.0
    )

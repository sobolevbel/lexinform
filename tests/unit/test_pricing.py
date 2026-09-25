import pytest

from lexinform.models import TokenUsage
from lexinform.pricing import batch_reservation, cost_usd, estimate_batch_reservation, price_of


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

from lexinform.models import TokenUsage
from lexinform.pricing import cost_usd, price_of


def test_prices_match_by_family_prefix() -> None:
    assert price_of("claude-opus-5") == (5.0, 25.0)
    assert price_of("claude-sonnet-5-20260601") == (2.0, 10.0)
    assert price_of("claude-haiku-4-5") == (1.0, 5.0)
    assert price_of("fake") is None


def test_cost_adds_models_and_cache_categories() -> None:
    usage = {
        # 287 711 uncached input + 334 output on Opus: the real druk 2695 analysis
        "claude-opus-5": TokenUsage(input=287_711, output=334),
        "claude-sonnet-5": TokenUsage(
            input=5_000, output=100, cache_read=1_000, cache_creation=500
        ),
    }
    cost = cost_usd(usage)
    assert cost is not None
    opus = 287_711 * 5 / 1e6 + 334 * 25 / 1e6
    sonnet = (5_000 * 2 + 1_000 * 2 * 0.1 + 500 * 2 * 1.25 + 100 * 10) / 1e6
    assert abs(cost - (opus + sonnet)) < 1e-9
    assert cost_usd({"claude-opus-5": TokenUsage(), "fake": TokenUsage(input=1)}) is None
    assert cost_usd({}) == 0.0

"""List prices of the Claude models (USD per million tokens), for the cost line of the run report.

Verified against platform.claude.com/docs/en/about-claude/pricing on 2026-09-08. Cache reads cost
0.1x the input price, cache writes 1.25x. Keys are matched as prefixes of the model id so dated
snapshots ("claude-sonnet-5-20260601") price like their family; an unknown model yields None and
the report simply shows no dollar figure.

`CHARS_PER_TOKEN` is what Polish legal text measures on Claude's tokenizer, which is what the
per-bill cost estimate is built on.
"""

from collections.abc import Mapping

from lexinform.models import TokenUsage

PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4": (1.0, 5.0),
}

CACHE_READ_FACTOR = 0.1
CACHE_WRITE_FACTOR = 1.25
CHARS_PER_TOKEN = 2.0
TOKENS_PER_SCANNED_PAGE = 1_600
"""A page of a Sejm scan, measured with `count_tokens` on 12 Sept 2026: druk 1273's government
position 16,157 tokens over 10 pages, its OSR 47,268 over 30, the opinion 1,622 over one."""


def estimate_input_cost(chars: int, input_price_per_mtok: float) -> float:
    """What sending `chars` of Polish text costs in input tokens, before the call is made."""
    return chars / CHARS_PER_TOKEN / 1_000_000 * input_price_per_mtok


def estimate_scan_cost(pages: int, input_price_per_mtok: float) -> float:
    """What sending a scanned document of `pages` costs, before the call is made."""
    return pages * TOKENS_PER_SCANNED_PAGE / 1_000_000 * input_price_per_mtok


def price_of(model: str) -> tuple[float, float] | None:
    for prefix, prices in sorted(PRICES.items(), key=lambda kv: -len(kv[0])):
        if model.startswith(prefix):
            return prices
    return None


def cost_usd(usage_by_model: Mapping[str, TokenUsage]) -> float | None:
    """Total cost of a run, or None when any model is not in the price list."""
    total = 0.0
    for model, usage in usage_by_model.items():
        prices = price_of(model)
        if prices is None:
            return None
        per_input, per_output = prices
        total += (
            usage.input * per_input
            + usage.cache_read * per_input * CACHE_READ_FACTOR
            + usage.cache_creation * per_input * CACHE_WRITE_FACTOR
            + usage.output * per_output
        ) / 1_000_000
    return total

"""List prices of the Claude and GPT models (USD per million tokens), for the cost line of the
run report.

Claude prices verified against platform.claude.com/docs/en/about-claude/pricing on 2026-09-08,
Opus 5.5 on 2026-09-23; cache reads cost 0.1x the input price, cache writes 1.25x. GPT-5.1
verified against developers.openai.com/api/docs/pricing on 2026-09-22 ($1.25/$10 standard,
cached input $0.125 — also 0.1x, so the same `CACHE_READ_FACTOR` prices it right; GPT-5.1 has no
billed cache-write step, so `cache_creation_input_tokens` is always 0 for it). Keys are matched as
prefixes of the model id so dated snapshots ("claude-sonnet-5-20260601", "gpt-5.1-2025-11-13")
price like their family; an unknown model yields None and the report simply shows no dollar figure.

`CHARS_PER_TOKEN` is what Polish legal text measures on Claude's tokenizer, which is what the
per-bill cost estimate is built on before a model is asked to count; GPT-5.1's own tokenizer
(`llm_openai.py`) is used instead wherever it is available, which is the normal case.
"""

from collections.abc import Mapping

from lexinform.models import TokenUsage

PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4": (1.0, 5.0),
    "gpt-5.1": (1.25, 10.0),
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


def input_cost(tokens: int, input_price_per_mtok: float) -> float:
    """What a counted input costs, with no estimating in between."""
    return tokens / 1_000_000 * input_price_per_mtok


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
            + (
                usage.batch_input * per_input
                + usage.batch_cache_read * per_input * CACHE_READ_FACTOR
                + usage.batch_cache_creation * per_input * CACHE_WRITE_FACTOR
                + usage.batch_output * per_output
            )
            * 0.5
        ) / 1_000_000
    return total


def format_usd(amount: float | None) -> str:
    """A cost as every message shows it: `$0.48`, and `$0.002` below a cent, because a run that
    cost a fifth of a cent must not read as `$0.00`. `$?` when the price list does not know the
    model that ran, which is what `cost_usd` says with None."""
    if amount is None:
        return "$?"
    return f"${amount:.2f}" if amount >= 0.01 or amount == 0 else f"${amount:.3f}"


def format_tokens(tokens: int) -> str:
    """A token count as every message shows it: `95.3k` from a thousand up, the number below."""
    return f"{tokens / 1000:.1f}k" if tokens >= 1000 else str(tokens)


def batch_reservation(model: str, *, input_tokens: int, max_output_tokens: int) -> float:
    prices = price_of(model)
    if prices is None:
        raise ValueError(f"cannot reserve batch cost for unknown model {model}")
    return (input_tokens * prices[0] + max_output_tokens * prices[1]) / 2_000_000

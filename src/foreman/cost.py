"""Cost accounting for budget guardrails (R5/§9).

The authoritative cost of a run is ``result.total_cost_usd`` from the stream
(captured live). To enforce a cost ceiling *mid-run* (before the result arrives)
we maintain a running estimate from per-message token usage and a small price
table. When the running estimate crosses the budget, Foreman kills the worker —
it does not wait for the agent to stop itself. The native ``--max-budget-usd``
flag is also passed as a second line of defence.
"""

from __future__ import annotations

from dataclasses import dataclass

from .stream_parser import Usage


@dataclass
class Price:
    """USD per 1M tokens."""

    input: float
    output: float
    cache_write: float
    cache_read: float


# Indicative prices per 1M tokens. Unknown models fall back to DEFAULT_PRICE.
# These drive the *mid-run estimate* only; the result event reconciles to actual.
PRICES: dict[str, Price] = {
    "claude-haiku-4-5-20251001": Price(1.0, 5.0, 1.25, 0.10),
    "claude-sonnet-4-6": Price(3.0, 15.0, 3.75, 0.30),
    "claude-opus-4-8": Price(15.0, 75.0, 18.75, 1.50),
    "claude-fable-5": Price(5.0, 25.0, 6.25, 0.50),
}
DEFAULT_PRICE = Price(5.0, 25.0, 6.25, 0.50)


class CostModel:
    """Estimates incremental USD cost from token usage."""

    def __init__(self, prices: dict[str, Price] | None = None):
        self.prices = prices if prices is not None else PRICES

    def price_for(self, model: str) -> Price:
        return self.prices.get(model, DEFAULT_PRICE)

    def estimate(self, usage: Usage, model: str) -> float:
        p = self.price_for(model)
        return (
            usage.input_tokens * p.input
            + usage.output_tokens * p.output
            + usage.cache_creation_input_tokens * p.cache_write
            + usage.cache_read_input_tokens * p.cache_read
        ) / 1_000_000.0

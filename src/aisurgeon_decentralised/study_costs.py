"""Versioned Phase-2 cost accounting without reasoning-token double counting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .study_phase2 import (
    PRICE_VERSION,
    STUDY_MAX_ESTIMATED_API_COST_USD,
    SUPERSEDED_STUDY_COST_CEILING_USD,
)
from .study_responses import ResponseUsage

PRICE_TABLE: dict[str, Any] = {
    "price_version": PRICE_VERSION,
    "effective_as_of": "2026-08-29",
    "currency": "USD",
    "source": "https://developers.openai.com/api/docs/pricing",
    "study_cost_ceiling_usd": STUDY_MAX_ESTIMATED_API_COST_USD,
    "supersedes_study_cost_ceiling_usd": SUPERSEDED_STUDY_COST_CEILING_USD,
    "cost_ceiling_scope": (
        "cumulative Phase 2 preparation, pilot, main-study attempts and retries"
    ),
    "web_search_usd_per_1000_calls": 10.0,
    "embedding": {"text-embedding-3-small": {"input_usd_per_million_tokens": 0.02}},
    "models": {
        "gpt-5.5-2026-04-23": {
            "input_usd_per_million_tokens": 5.0,
            "cached_input_usd_per_million_tokens": 0.5,
            "cache_write_usd_per_million_tokens": 5.0,
            "output_usd_per_million_tokens": 30.0,
        },
        "gpt-5.6-sol": {
            "input_usd_per_million_tokens": 4.0,
            "cached_input_usd_per_million_tokens": 0.4,
            "cache_write_usd_per_million_tokens": 5.0,
            "output_usd_per_million_tokens": 20.0,
        },
    },
    "accounting_notes": [
        "Reasoning tokens are a subset of output tokens and are never charged twice.",
        "Search-content tokens are billed at model input rates and are assumed to be included in Responses input-token usage unless separately reported as excluded.",
        "If cache-write tokens are reported, they are removed from ordinary uncached input before applying the cache-write rate.",
        "Prices are frozen for prospective immediate estimates; invoice reconciliation is a separate later step.",
    ],
}


@dataclass(frozen=True)
class CostBreakdown:
    model_cost_usd: float
    web_search_cost_usd: float
    embedding_cost_usd: float
    retry_cost_usd: float
    total_estimated_cost_usd: float
    standardized_uncached_cost_usd: float
    reconciled_cost_usd: float | None
    cost_reconciliation_status: str
    price_version: str = PRICE_VERSION


def calculate_cost(
    *,
    model: str,
    usage: ResponseUsage,
    web_search_calls: int,
    embedding_tokens: int = 0,
    is_retry_attempt: bool = False,
) -> CostBreakdown:
    rates = PRICE_TABLE["models"].get(model)
    if rates is None:
        raise ValueError(f"no frozen price for requested model: {model}")
    # input_tokens includes cached and (when exposed) cache-write input.
    ordinary_uncached = max(
        usage.input_tokens - usage.cached_input_tokens - usage.cache_write_tokens,
        0,
    )
    model_cost = (
        ordinary_uncached * rates["input_usd_per_million_tokens"]
        + usage.cached_input_tokens * rates["cached_input_usd_per_million_tokens"]
        + usage.cache_write_tokens * rates["cache_write_usd_per_million_tokens"]
        + usage.output_tokens * rates["output_usd_per_million_tokens"]
    ) / 1_000_000
    web_cost = web_search_calls * PRICE_TABLE["web_search_usd_per_1000_calls"] / 1000
    embedding_cost = (
        embedding_tokens
        * PRICE_TABLE["embedding"]["text-embedding-3-small"][
            "input_usd_per_million_tokens"
        ]
        / 1_000_000
    )
    total = model_cost + web_cost + embedding_cost
    standardized = (
        (
            usage.input_tokens * rates["input_usd_per_million_tokens"]
            + usage.output_tokens * rates["output_usd_per_million_tokens"]
        )
        / 1_000_000
        + web_cost
        + embedding_cost
    )
    return CostBreakdown(
        model_cost_usd=model_cost,
        web_search_cost_usd=web_cost,
        embedding_cost_usd=embedding_cost,
        retry_cost_usd=total if is_retry_attempt else 0.0,
        total_estimated_cost_usd=total,
        standardized_uncached_cost_usd=standardized,
        reconciled_cost_usd=None,
        cost_reconciliation_status="not_reconciled_admin_key_not_required",
    )


__all__ = ["PRICE_TABLE", "CostBreakdown", "calculate_cost"]

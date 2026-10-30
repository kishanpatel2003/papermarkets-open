"""Aggregate metrics from a simulation trade log."""

from __future__ import annotations

import statistics
from collections import Counter

from papermarkets.simulator.result import SimulatedTrade


def compute_metrics(trades: list[SimulatedTrade]) -> dict:
    """Compute aggregate execution quality metrics from the trade log.

    Returns a dict with: median_slippage_pct, p95_slippage_pct,
    median_book_staleness_ms, p95_book_staleness_ms, pct_high_confidence,
    skip_reasons.
    """
    filled = [t for t in trades if not t.skipped]
    skipped = [t for t in trades if t.skipped]

    skip_reasons = dict(Counter(t.skip_reason for t in skipped if t.skip_reason))

    if not filled:
        return {
            "median_slippage_pct": 0.0,
            "p95_slippage_pct": 0.0,
            "median_book_staleness_ms": 0.0,
            "p95_book_staleness_ms": 0.0,
            "pct_high_confidence": 0.0,
            "skip_reasons": skip_reasons,
        }

    slippages = [t.slippage_pct for t in filled]
    staleness = [float(t.book_staleness_ms) for t in filled]

    high_count = sum(1 for t in filled if t.confidence == "high")

    return {
        "median_slippage_pct": statistics.median(slippages),
        "p95_slippage_pct": _percentile(slippages, 95),
        "median_book_staleness_ms": statistics.median(staleness),
        "p95_book_staleness_ms": _percentile(staleness, 95),
        "pct_high_confidence": high_count / len(filled),
        "skip_reasons": skip_reasons,
    }


def compute_resolved_win_rate(
    resolved_pnl_by_market: dict[str, float],
) -> float:
    """Fraction of resolved markets with positive P&L."""
    if not resolved_pnl_by_market:
        return 0.0
    wins = sum(1 for pnl in resolved_pnl_by_market.values() if pnl > 0)
    return wins / len(resolved_pnl_by_market)


def _percentile(data: list[float], pct: int) -> float:
    """Compute percentile using sorted interpolation."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (pct / 100) * (len(sorted_data) - 1)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[-1]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])

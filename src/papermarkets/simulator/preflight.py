"""Pre-simulation data coverage check."""

from __future__ import annotations

import logging
import statistics

import duckdb

from papermarkets.simulator.result import PreflightReport

logger = logging.getLogger(__name__)


def run_preflight(
    con: duckdb.DuckDBPyConnection,
    wallet: str,
) -> PreflightReport:
    """Scan data coverage for a wallet before running the simulation."""
    from papermarkets.queries.v1_queries import wallet_market_coverage

    coverage = wallet_market_coverage(con, wallet)

    total_trades = sum(m["trade_count"] for m in coverage)
    markets_traded = len(coverage)
    covered = [m for m in coverage if m["has_orderbook_data"]]
    uncovered = [m for m in coverage if not m["has_orderbook_data"]]
    trades_covered = sum(m["trade_count"] for m in covered)
    trades_uncovered = sum(m["trade_count"] for m in uncovered)

    # Count resolved markets by inferring from last trade price
    resolved_count = 0
    for m in coverage:
        if not m["is_closed"]:
            continue
        outcome = _infer_resolution(con, m["market_id"])
        if outcome is not None:
            resolved_count += 1

    # Median snapshot gap per covered market
    median_gaps: dict[str, float] = {}
    for m in covered:
        gap = _median_snapshot_gap(con, m["market_id"])
        if gap is not None:
            median_gaps[m["market_slug"]] = gap

    coverage_pct = trades_covered / total_trades if total_trades > 0 else 0.0

    return PreflightReport(
        wallet=wallet,
        total_whale_trades=total_trades,
        markets_traded=markets_traded,
        markets_with_orderbook_data=len(covered),
        markets_without_orderbook_data=len(uncovered),
        trades_in_covered_markets=trades_covered,
        trades_in_uncovered_markets=trades_uncovered,
        coverage_pct=coverage_pct,
        resolved_markets=resolved_count,
        median_snapshot_gap_ms=median_gaps,
    )


def infer_resolution(con: duckdb.DuckDBPyConnection, market_id: str) -> str | None:
    """Infer market resolution from last trade price. Public interface."""
    return _infer_resolution(con, market_id)


def _infer_resolution(con: duckdb.DuckDBPyConnection, market_id: str) -> str | None:
    """Infer resolution outcome from the last trade price in a market.

    Returns "Yes", "No", or None if unresolvable.
    """
    row = con.execute(
        """
        SELECT token_label, price
        FROM trades
        WHERE market_id = $mid
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        {"mid": market_id},
    ).fetchone()
    if row is None:
        return None
    token_label, price = row
    if token_label == "Yes":
        if price > 0.90:
            return "Yes"
        if price < 0.10:
            return "No"
    elif token_label == "No":
        if price > 0.90:
            return "No"
        if price < 0.10:
            return "Yes"
    return None


def _median_snapshot_gap(con: duckdb.DuckDBPyConnection, market_id: str) -> float | None:
    """Compute median time gap between consecutive orderbook snapshots (ms)."""
    rows = con.execute(
        """
        SELECT epoch_ms(timestamp) AS ts_ms
        FROM orderbooks
        WHERE market_id = $mid
        ORDER BY timestamp
        """,
        {"mid": market_id},
    ).fetchall()
    if len(rows) < 2:
        return None
    timestamps = [r[0] for r in rows]
    gaps = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
    return statistics.median(gaps)

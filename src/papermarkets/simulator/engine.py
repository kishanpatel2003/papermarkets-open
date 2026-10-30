"""Core simulation loop for copy-trading."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from papermarkets.queries.connection import get_connection
from papermarkets.queries.v1_queries import orderbook_after_time, trades_by_wallet
from papermarkets.simulator.book_walker import parse_book_side, simulate_fill
from papermarkets.simulator.config import SimulationConfig
from papermarkets.simulator.metrics import compute_metrics, compute_resolved_win_rate
from papermarkets.simulator.position_tracker import PositionTracker
from papermarkets.simulator.preflight import infer_resolution, run_preflight
from papermarkets.simulator.result import SimulatedTrade, SimulationResult

logger = logging.getLogger(__name__)


def run_simulation(config: SimulationConfig, data_dir: Path) -> SimulationResult:
    """Run the full copy-trading simulation."""
    con = get_connection(data_dir)

    preflight = run_preflight(con, config.wallet)
    logger.info(
        "Preflight: %d trades across %d markets (%d with orderbooks)",
        preflight.total_whale_trades,
        preflight.markets_traded,
        preflight.markets_with_orderbook_data,
    )

    # Build market_id → slug lookup
    market_lookup = _build_market_lookup(con, config.wallet)

    # Fetch whale trades
    whale_trades = trades_by_wallet(con, config.wallet, config.start, config.end).fetchall()
    trade_columns = [desc[0] for desc in con.description]

    tracker = PositionTracker(config.bankroll)
    trade_log: list[SimulatedTrade] = []
    delay_ms = config.detection_delay_ms + config.execution_latency_ms

    for row in whale_trades:
        trade = dict(zip(trade_columns, row, strict=True))
        sim_trade = _process_whale_trade(con, config, trade, tracker, market_lookup, delay_ms)
        trade_log.append(sim_trade)

    # Settle resolved markets
    resolved_pnl_by_market = _settle_resolved_markets(con, tracker, market_lookup)

    # Mark-to-market unresolved positions
    unresolved_pnl, unresolved_market_ids = _mark_to_market(con, tracker)

    # Compute aggregate metrics
    metrics = compute_metrics(trade_log)
    resolved_win_rate = compute_resolved_win_rate(resolved_pnl_by_market)
    resolved_pnl_total = sum(resolved_pnl_by_market.values())

    filled = [t for t in trade_log if not t.skipped]
    skipped = [t for t in trade_log if t.skipped]

    total_pnl = resolved_pnl_total + unresolved_pnl
    total_pnl_pct = total_pnl / config.bankroll if config.bankroll > 0 else 0.0

    return SimulationResult(
        config=config,
        preflight=preflight,
        trades=trade_log,
        resolved_pnl=resolved_pnl_total,
        resolved_markets=len(resolved_pnl_by_market),
        resolved_win_rate=resolved_win_rate,
        unresolved_pnl=unresolved_pnl,
        unresolved_markets=len(unresolved_market_ids),
        starting_bankroll=config.bankroll,
        ending_bankroll=tracker.bankroll,
        total_pnl=total_pnl,
        total_pnl_pct=total_pnl_pct,
        total_trades_attempted=len(trade_log),
        total_trades_filled=len(filled),
        total_trades_skipped=len(skipped),
        skip_reasons=metrics["skip_reasons"],
        median_slippage_pct=metrics["median_slippage_pct"],
        p95_slippage_pct=metrics["p95_slippage_pct"],
        median_book_staleness_ms=metrics["median_book_staleness_ms"],
        p95_book_staleness_ms=metrics["p95_book_staleness_ms"],
        pct_high_confidence=metrics["pct_high_confidence"],
        max_drawdown=tracker.max_drawdown,
        max_drawdown_pct=tracker.max_drawdown_pct,
    )


def _process_whale_trade(
    con: duckdb.DuckDBPyConnection,
    config: SimulationConfig,
    trade: dict,
    tracker: PositionTracker,
    market_lookup: dict[str, str],
    delay_ms: int,
) -> SimulatedTrade:
    """Process a single whale trade and return a SimulatedTrade (filled or skipped)."""
    market_id = trade["market_id"]
    token_id = trade["token_id"]
    token_label = trade["token_label"]
    whale_side = trade["side"]
    whale_price = float(trade["price"])
    whale_size = float(trade["size"])
    whale_ts = trade["timestamp"]
    market_slug = market_lookup.get(market_id, market_id)

    # Ensure whale_ts is a datetime
    if isinstance(whale_ts, (int, float)):
        whale_dt = datetime.fromtimestamp(whale_ts, tz=UTC)
    elif isinstance(whale_ts, datetime):
        whale_dt = whale_ts if whale_ts.tzinfo else whale_ts.replace(tzinfo=UTC)
    else:
        whale_dt = datetime.fromtimestamp(float(whale_ts), tz=UTC)

    copier_entry_time = whale_dt + timedelta(milliseconds=delay_ms)

    # Helper to create a skipped trade
    def skipped(reason: str) -> SimulatedTrade:
        return SimulatedTrade(
            whale_timestamp=whale_dt,
            whale_market_id=market_id,
            whale_market_slug=market_slug,
            whale_token_label=token_label,
            whale_side=whale_side,
            whale_price=whale_price,
            whale_size=whale_size,
            copier_entry_time=copier_entry_time,
            copier_side=whale_side,
            copier_order_size=0.0,
            copier_fill_size=0.0,
            copier_fill_price=0.0,
            copier_total_cost=0.0,
            slippage_vs_whale=0.0,
            slippage_pct=0.0,
            book_staleness_ms=0,
            levels_consumed=0,
            fully_filled=False,
            confidence="low",
            bankroll_after=tracker.bankroll,
            skipped=True,
            skip_reason=reason,
        )

    # SELL requires existing position
    if whale_side == "SELL" and not tracker.has_position(market_id, token_label):
        return skipped("no_position_to_sell")

    # Find orderbook snapshot after copier entry time
    snapshot = orderbook_after_time(
        con, market_id, token_id, copier_entry_time, config.max_book_staleness_ms
    )
    if snapshot is None:
        return skipped("no_orderbook_data")

    # Compute book staleness
    snap_ts = snapshot["timestamp"]
    snap_ms = int(snap_ts.timestamp() * 1000) if isinstance(snap_ts, datetime) else int(snap_ts)
    copier_ms = int(copier_entry_time.timestamp() * 1000)
    book_staleness_ms = snap_ms - copier_ms

    # Confidence scoring
    if book_staleness_ms < 10_000:
        confidence = "high"
    elif book_staleness_ms < 60_000:
        confidence = "medium"
    else:
        confidence = "low"

    # Position sizing
    best_ask = float(snapshot.get("best_ask", 0) or 0)
    if whale_side == "BUY":
        if config.position_size_mode == "fixed_usd":
            if best_ask <= 0:
                return skipped("no_liquidity")
            order_size = config.position_size_value / best_ask
        else:
            order_size = config.position_size_value

        # Max position pct limit
        market_exposure = sum(
            pos.cost_basis for (mid, _), pos in tracker.all_positions.items() if mid == market_id
        )
        max_allowed = config.max_position_pct * tracker.bankroll
        available = max(0.0, max_allowed - market_exposure)
        if best_ask > 0:
            max_shares = available / best_ask
            order_size = min(order_size, max_shares)

        # Bankroll limit
        if best_ask > 0:
            affordable = tracker.bankroll / best_ask
            order_size = min(order_size, affordable)

        if order_size <= 0:
            return skipped("insufficient_bankroll")
    else:
        # SELL: sell what we hold, up to position size
        held = tracker.get_position(market_id, token_label).net_shares
        if config.position_size_mode == "fixed_usd":
            best_bid = float(snapshot.get("best_bid", 0) or 0)
            if best_bid <= 0:
                return skipped("no_liquidity")
            order_size = min(config.position_size_value / best_bid, held)
        else:
            order_size = min(config.position_size_value, held)

    if order_size <= 0:
        return skipped("insufficient_bankroll")

    # Walk the book
    bids = parse_book_side(snapshot.get("bids", "[]"), descending=True)
    asks = parse_book_side(snapshot.get("asks", "[]"), descending=False)
    fill = simulate_fill(whale_side, order_size, bids, asks)

    if fill.fill_size == 0:
        return skipped("no_liquidity")

    # Partial fill policy
    if fill.unfilled_size > 0 and config.partial_fill_policy == "reject":
        return skipped("partial_fill_rejected")

    # Slippage check
    slippage_vs_whale = fill.fill_price - whale_price
    slippage_pct = abs(slippage_vs_whale) / whale_price if whale_price > 0 else 0.0
    if slippage_pct > config.max_slippage_pct:
        return skipped("slippage_exceeded")

    # Execute the trade
    fee = fill.total_cost * config.fee_rate if whale_side == "BUY" else 0.0
    sell_proceeds = fill.fill_size * fill.fill_price
    sell_fee = sell_proceeds * config.fee_rate if whale_side == "SELL" else 0.0

    total_cost_with_fee = fill.total_cost + fee if whale_side == "BUY" else 0.0
    if whale_side == "BUY" and total_cost_with_fee > tracker.bankroll + 1e-9:
        return skipped("insufficient_bankroll")

    tracker.record_trade(
        market_id=market_id,
        token_label=token_label,
        side=whale_side,
        size=fill.fill_size,
        price=fill.fill_price,
        fee=fee if whale_side == "BUY" else sell_fee,
    )

    return SimulatedTrade(
        whale_timestamp=whale_dt,
        whale_market_id=market_id,
        whale_market_slug=market_slug,
        whale_token_label=token_label,
        whale_side=whale_side,
        whale_price=whale_price,
        whale_size=whale_size,
        copier_entry_time=copier_entry_time,
        copier_side=whale_side,
        copier_order_size=order_size,
        copier_fill_size=fill.fill_size,
        copier_fill_price=fill.fill_price,
        copier_total_cost=fill.total_cost,
        slippage_vs_whale=slippage_vs_whale,
        slippage_pct=slippage_pct,
        book_staleness_ms=book_staleness_ms,
        levels_consumed=fill.levels_consumed,
        fully_filled=fill.unfilled_size == 0,
        confidence=confidence,
        bankroll_after=tracker.bankroll,
    )


def _build_market_lookup(con: duckdb.DuckDBPyConnection, wallet: str) -> dict[str, str]:
    """Build market_id → slug mapping for markets the wallet traded."""
    rows = con.execute(
        """
        SELECT DISTINCT m.market_id, m.slug
        FROM trades t
        JOIN markets m ON t.market_id = m.market_id
        WHERE t.user_address = $wallet
        """,
        {"wallet": wallet},
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def _settle_resolved_markets(
    con: duckdb.DuckDBPyConnection,
    tracker: PositionTracker,
    market_lookup: dict[str, str],
) -> dict[str, float]:
    """Settle positions in resolved markets. Returns market_id → resolution P&L."""
    resolved_pnl: dict[str, float] = {}

    # Find all markets with open positions
    market_ids = {mid for (mid, _) in tracker.all_positions}

    for market_id in market_ids:
        # Check if market is closed
        row = con.execute(
            "SELECT closed FROM markets WHERE market_id = $mid",
            {"mid": market_id},
        ).fetchone()
        if row is None or not row[0]:
            continue

        outcome = infer_resolution(con, market_id)
        if outcome is None:
            continue

        pnl = tracker.resolve_market(market_id, outcome)
        resolved_pnl[market_id] = pnl

    return resolved_pnl


def _mark_to_market(
    con: duckdb.DuckDBPyConnection,
    tracker: PositionTracker,
) -> tuple[float, set[str]]:
    """Mark-to-market unresolved positions. Returns (total_mtm_pnl, market_ids)."""
    total_mtm = 0.0
    market_ids: set[str] = set()

    for (market_id, token_label), pos in tracker.all_positions.items():
        if pos.net_shares < 1e-9:
            continue

        mid_price = _last_mid_price(con, market_id, token_label)
        mtm = pos.net_shares * mid_price - pos.cost_basis if mid_price is not None else 0.0

        total_mtm += mtm
        market_ids.add(market_id)

    return total_mtm, market_ids


def _last_mid_price(
    con: duckdb.DuckDBPyConnection,
    market_id: str,
    token_label: str,
) -> float | None:
    """Get last known mid price for a token, trying prices table then orderbooks."""
    # Try prices table first
    try:
        row = con.execute(
            """
            SELECT mid_price FROM prices
            WHERE market_id = $mid
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            {"mid": market_id},
        ).fetchone()
        if row and row[0] is not None:
            price = float(row[0])
            # If this is the No token, invert the price (prices track Yes token)
            if token_label == "No":
                return 1.0 - price
            return price
    except duckdb.CatalogException:
        pass

    # Fall back to orderbook mid_price
    try:
        row = con.execute(
            """
            SELECT mid_price FROM orderbooks
            WHERE market_id = $mid
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            {"mid": market_id},
        ).fetchone()
        if row and row[0] is not None:
            price = float(row[0])
            if token_label == "No":
                return 1.0 - price
            return price
    except duckdb.CatalogException:
        pass

    return None

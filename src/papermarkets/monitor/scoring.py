"""Suspicion scoring engine: pure functions that score insider-trading signals."""

from __future__ import annotations

from dataclasses import dataclass

from papermarkets.monitor.state import WalletProfile

WEIGHTS: dict[str, float] = {
    "freshness": 0.20,
    "position_size": 0.20,
    "conviction": 0.25,
    "concentration": 0.15,
    "coordination": 0.10,
    "price_impact": 0.10,
}


@dataclass
class SuspicionScore:
    freshness: float
    position_size: float
    conviction: float
    concentration: float
    coordination: float
    price_impact: float
    composite: float


def score_freshness(wallet_profile: WalletProfile | None) -> float:
    if wallet_profile is None:
        return 1.0
    trades = wallet_profile.total_trades
    if trades < 10:
        return 0.8
    if trades < 50:
        return 0.5
    if trades < 200:
        return 0.2
    return 0.0


def score_position_size(trade_size: float, trade_price: float, book: dict | None) -> float:
    """Score how much of the near-side orderbook depth a trade consumes.

    Args:
        trade_size: number of shares traded
        trade_price: price per share
        book: {"bids": [[price, size], ...], "asks": [[price, size], ...]} or None
    """
    notional = trade_size * trade_price
    if notional < 10.0:
        return 0.0
    if book is None:
        return 0.5

    # Sum near-side depth within 2 cents of trade price
    depth = 0.0
    for level in book.get("bids", []) + book.get("asks", []):
        level_price, level_size = level[0], level[1]
        if abs(level_price - trade_price) <= 0.02:
            depth += level_size * level_price

    if depth <= 0:
        return 0.5

    ratio = notional / depth
    if ratio > 0.50:
        return 1.0
    if ratio > 0.25:
        return 0.7
    if ratio > 0.10:
        return 0.4
    return 0.1


def score_conviction(trade_price: float, current_mid_price: float, side: str) -> float:
    """Score conviction based on buying low-probability outcomes."""
    if side.upper() != "BUY":
        return 0.0

    if current_mid_price < 0.05:
        base = 1.0
    elif current_mid_price < 0.10:
        base = 0.8
    elif current_mid_price < 0.20:
        base = 0.5
    elif current_mid_price < 0.40:
        base = 0.2
    else:
        return 0.0

    if trade_price > current_mid_price and current_mid_price > 0:
        overpay = min(trade_price / current_mid_price, 2.0)
        base *= overpay

    return min(base, 2.0)


def score_concentration(wallet_positions: dict[str, float]) -> float:
    """Score how concentrated a wallet's volume is on a single outcome."""
    if not wallet_positions:
        return 0.0

    total = sum(wallet_positions.values())
    if total <= 0:
        return 0.0

    max_share = max(wallet_positions.values()) / total
    n_outcomes = len(wallet_positions)

    if max_share >= 1.0:
        return 1.0
    if max_share > 0.80:
        return 0.7
    if max_share > 0.60:
        return 0.4
    if n_outcomes >= 3:
        return 0.0
    return 0.2


def score_coordination(token_id: str, trade_windows: list[tuple[int, str, float]]) -> float:
    """Score coordinated buying from multiple fresh wallets.

    Args:
        token_id: the token being traded (unused but kept for API clarity)
        trade_windows: list of (timestamp, wallet_address, freshness_score) within last 30 min
    """
    fresh_wallets = {addr for _, addr, freshness in trade_windows if freshness > 0.5}
    count = len(fresh_wallets)
    if count >= 5:
        return 1.0
    if count >= 3:
        return 0.7
    if count >= 2:
        return 0.3
    return 0.0


def score_price_impact(price_before: float, price_after: float, side: str) -> float:
    """Score based on how much the trade moved the price."""
    delta = price_after - price_before if side.upper() == "BUY" else price_before - price_after

    if delta > 0.05:
        return 1.0
    if delta > 0.03:
        return 0.7
    if delta > 0.01:
        return 0.3
    return 0.0


def compute_suspicion(signals: dict[str, float]) -> SuspicionScore:
    """Compute weighted composite score from individual signal scores."""
    composite = sum(signals.get(k, 0.0) * w for k, w in WEIGHTS.items())
    return SuspicionScore(
        freshness=signals.get("freshness", 0.0),
        position_size=signals.get("position_size", 0.0),
        conviction=signals.get("conviction", 0.0),
        concentration=signals.get("concentration", 0.0),
        coordination=signals.get("coordination", 0.0),
        price_impact=signals.get("price_impact", 0.0),
        composite=composite,
    )

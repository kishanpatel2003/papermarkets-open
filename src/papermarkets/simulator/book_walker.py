"""Orderbook fill simulation via book walking."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class FillResult:
    """Result of walking an orderbook to fill an order."""

    fill_price: float  # VWAP across consumed levels (0.0 if no fill)
    fill_size: float  # Total shares filled
    unfilled_size: float  # Remaining unfilled shares
    levels_consumed: int  # How many price levels were touched
    total_cost: float  # fill_size * fill_price
    fills: list[tuple[float, float]] = field(default_factory=list)  # [(price, size), ...]


_EMPTY_FILL = FillResult(
    fill_price=0.0, fill_size=0.0, unfilled_size=0.0, levels_consumed=0, total_cost=0.0
)


def parse_book_side(json_str: str, descending: bool = False) -> list[tuple[float, float]]:
    """Parse a JSON array of {price, size} into sorted (price, size) tuples.

    Args:
        json_str: JSON string like '[{"price": "0.62", "size": "200.0"}, ...]'
        descending: If True, sort by price descending (for bids). Default ascending (for asks).

    Returns:
        List of (price, size) tuples, sorted by price.
    """
    try:
        raw = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse book side JSON")
        return []

    levels: list[tuple[float, float]] = []
    for entry in raw:
        try:
            price = float(entry["price"])
            size = float(entry["size"])
            levels.append((price, size))
        except (KeyError, ValueError, TypeError):
            continue

    levels.sort(key=lambda x: x[0], reverse=descending)
    return levels


def simulate_fill(
    side: str,
    order_size: float,
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
) -> FillResult:
    """Simulate a market order against an orderbook snapshot.

    BUY: walks asks lowest-to-highest.
    SELL: walks bids highest-to-lowest.
    """
    if order_size <= 0:
        return _EMPTY_FILL

    book = asks if side == "BUY" else bids
    remaining = order_size
    total_cost = 0.0
    fills: list[tuple[float, float]] = []

    for price, size in book:
        if remaining <= 0:
            break
        take = min(remaining, size)
        total_cost += price * take
        fills.append((price, take))
        remaining -= take

    fill_size = order_size - remaining
    fill_price = total_cost / fill_size if fill_size > 0 else 0.0

    return FillResult(
        fill_price=fill_price,
        fill_size=fill_size,
        unfilled_size=remaining,
        levels_consumed=len(fills),
        total_cost=total_cost,
        fills=fills,
    )

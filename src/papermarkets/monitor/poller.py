"""Poll Dome API for new trades and orderbook snapshots."""

import logging
import time
from datetime import UTC, datetime

from papermarkets.ingest.client import HttpClient, dome_client
from papermarkets.ingest.config import DOME_BASE_URL
from papermarkets.ingest.exceptions import SkipMarket
from papermarkets.ingest.pagination import paginate_dome
from papermarkets.monitor.config import Outcome
from papermarkets.monitor.state import MonitorState, OrderbookSnapshot, TradeEvent

logger = logging.getLogger(__name__)


def _normalize_trade(order: dict, outcome_name: str = "") -> TradeEvent:
    """Convert a raw Dome trade dict to a TradeEvent. Timestamps in seconds."""
    return TradeEvent(
        trade_id=order["order_hash"],
        market_id=order["condition_id"],
        token_id=order["token_id"],
        token_label=order["token_label"],
        side=order["side"],
        price=float(order["price"]),
        size=float(order["shares_normalized"]),
        timestamp=order["timestamp"],  # seconds
        user_address=order["user"],
        taker_address=order.get("taker", ""),
        tx_hash=order.get("tx_hash", ""),
        block_number=int(order.get("block_number", 0)),
        outcome_name=outcome_name,
    )


def _poll_token_trades(
    token_id: str,
    since_ts: int,
    outcome_name: str,
    client: HttpClient,
) -> list[TradeEvent]:
    """Fetch trades for a single token since a given timestamp."""
    now_s = int(time.time())
    url = (
        f"{DOME_BASE_URL}/polymarket/orders"
        f"?token_id={token_id}"
        f"&start_time={since_ts}"
        f"&end_time={now_s}"
        f"&limit=1000"
    )
    trades = []
    try:
        for page in paginate_dome(client, url, items_field="orders"):
            for order in page:
                trades.append(_normalize_trade(order, outcome_name))
    except SkipMarket:
        logger.debug("No trades for token %s...%s", token_id[:8], token_id[-4:])
    return trades


def poll_trades(
    outcomes: list[Outcome],
    state: MonitorState,
    client: HttpClient | None = None,
) -> list[TradeEvent]:
    """Fetch new trades for all active outcomes since last poll.

    Uses dome_client.get_many() indirectly — we build per-token URLs and
    fetch sequentially per outcome to use pagination properly.
    """
    client = client or dome_client
    new_trades: list[TradeEvent] = []

    for outcome in outcomes:
        for token_id in (outcome.yes_token_id, outcome.no_token_id):
            if not token_id:
                continue
            since = state.last_trade_ts.get(token_id, int(time.time()) - 300)
            token_trades = _poll_token_trades(token_id, since, outcome.name, client)
            for t in token_trades:
                if t.trade_id not in state.seen_trade_ids:
                    state.seen_trade_ids.add(t.trade_id)
                    new_trades.append(t)
                    # Track position deltas
                    addr = t.user_address
                    if addr not in state.wallet_positions:
                        state.wallet_positions[addr] = {}
                    delta = t.size if t.side == "BUY" else -t.size
                    state.wallet_positions[addr][t.token_id] = (
                        state.wallet_positions[addr].get(t.token_id, 0) + delta
                    )
            # Update high-water mark
            if token_trades:
                max_ts = max(t.timestamp for t in token_trades)
                state.last_trade_ts[token_id] = max(
                    state.last_trade_ts.get(token_id, 0), max_ts + 1
                )

    logger.info(
        "Polled trades: %d new across %d outcomes",
        len(new_trades),
        len(outcomes),
    )
    return new_trades


def _fetch_latest_book(
    token_id: str,
    client: HttpClient,
) -> OrderbookSnapshot | None:
    """Fetch the most recent orderbook snapshot for a token."""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 120_000  # last 2 minutes
    url = (
        f"{DOME_BASE_URL}/polymarket/orderbooks"
        f"?token_id={token_id}"
        f"&start_time={start_ms}"
        f"&end_time={now_ms}"
        f"&limit=1"
    )
    try:
        data = client.get(url)
    except SkipMarket:
        return None

    snapshots = data.get("snapshots", [])
    if not snapshots:
        return None

    snap = snapshots[0]
    return OrderbookSnapshot(
        token_id=token_id,
        timestamp=snap["timestamp"],  # milliseconds
        bids=[[float(b["price"]), float(b["size"])] for b in snap.get("bids", [])],
        asks=[[float(a["price"]), float(a["size"])] for a in snap.get("asks", [])],
    )


def _mid_price(book: OrderbookSnapshot) -> float | None:
    """Calculate mid price from best bid and ask."""
    best_bid = book.bids[0][0] if book.bids else None
    best_ask = book.asks[0][0] if book.asks else None
    if best_bid is not None and best_ask is not None:
        return (best_bid + best_ask) / 2
    return best_bid or best_ask


def poll_orderbooks(
    outcomes: list[Outcome],
    state: MonitorState,
    client: HttpClient | None = None,
) -> None:
    """Fetch latest orderbook snapshot for each active outcome's YES token.

    Updates state.latest_books and state.current_prices in place.
    """
    client = client or dome_client
    token_ids = [o.yes_token_id for o in outcomes if o.yes_token_id]

    updated = 0
    for tid in token_ids:
        book = _fetch_latest_book(tid, client)
        if book is None:
            continue
        state.latest_books[tid] = book
        mid = _mid_price(book)
        if mid is not None:
            state.current_prices[tid] = mid
        updated += 1

    logger.info(
        "Polled orderbooks: %d/%d updated (%s)",
        updated,
        len(token_ids),
        datetime.now(tz=UTC).strftime("%H:%M:%S"),
    )

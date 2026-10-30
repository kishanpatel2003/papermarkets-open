"""Trade correlation engine: bridges fast CLOB events with Dome wallet identity."""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from papermarkets.monitor.alerts import format_alert, log_alert, push_alert
from papermarkets.monitor.config import Outcome
from papermarkets.monitor.display import format_trade_line
from papermarkets.monitor.scoring import (
    compute_suspicion,
    score_concentration,
    score_conviction,
    score_coordination,
    score_freshness,
    score_position_size,
    score_price_impact,
)
from papermarkets.monitor.state import (
    Alert,
    ClobTradeEvent,
    MonitorState,
    OrderbookSnapshot,
    TradeEvent,
    WalletSuspicion,
)
from papermarkets.monitor.wallet import WalletCache

if TYPE_CHECKING:
    from papermarkets.monitor.events import EventBus

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]
_ALERT_LOG_PATH = _ROOT / "data" / "monitor" / "alerts.jsonl"

# ANSI
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"


class TradeCorrelator:
    """Correlates fast CLOB trade events with slower Dome REST trade data.

    CLOB WebSocket provides instant trade detection (~5ms) but no wallet identity.
    Dome REST provides wallet addresses but with ~15s polling delay.
    The correlator matches these two streams to get both speed and identity.
    """

    def __init__(
        self,
        state: MonitorState,
        wallet_cache: WalletCache,
        outcomes: list[Outcome],
        alert_threshold: float = 0.2,
        no_sound: bool = False,
        ntfy_topic: str = "",
        event_bus: EventBus | None = None,
    ) -> None:
        self._state = state
        self._wallet_cache = wallet_cache
        self._outcomes = outcomes
        self._alert_threshold = alert_threshold
        self._no_sound = no_sound
        self._ntfy_topic = ntfy_topic
        self._event_bus = event_bus
        self._pending: list[ClobTradeEvent] = []
        self._lock = threading.Lock()

        # Build token_id -> outcome_name lookup
        self._token_to_outcome: dict[str, str] = {}
        for o in outcomes:
            if o.yes_token_id:
                self._token_to_outcome[o.yes_token_id] = o.name
            if o.no_token_id:
                self._token_to_outcome[o.no_token_id] = o.name

    def _publish(self, event_type_name: str, data: dict) -> None:
        """Publish an event to the bus if one is configured."""
        if self._event_bus is None:
            return
        from papermarkets.monitor.events import EventType, MonitorEvent

        self._event_bus.publish(MonitorEvent(type=EventType[event_type_name], data=data))

    def on_clob_trade(self, event: ClobTradeEvent) -> None:
        """Handle instant trade from CLOB WebSocket (no wallet address).

        Buffers the event for later correlation and prints a fast alert
        if the trade looks anomalous.
        """
        with self._lock:
            self._pending.append(event)

        outcome_name = self._token_to_outcome.get(event.token_id, "Unknown")
        mid_price = self._state.current_prices.get(event.token_id, event.price)

        notional = event.size * event.price
        is_anomalous = (
            notional > 500 and mid_price < 0.15 and event.side == "BUY"
        ) or notional > 2000

        self._publish(
            "FAST_TRADE",
            {
                "token_id": event.token_id,
                "price": event.price,
                "size": event.size,
                "side": event.side,
                "timestamp": event.timestamp,
                "outcome_name": outcome_name,
            },
        )

        if is_anomalous:
            self._print_fast_alert(event, outcome_name, notional)

    def _print_fast_alert(self, event: ClobTradeEvent, outcome_name: str, notional: float) -> None:
        """Print a preliminary alert before wallet identity is known."""
        ts = datetime.fromtimestamp(event.timestamp / 1000, tz=UTC).strftime("%H:%M:%S")
        print(
            f"{_YELLOW}{_BOLD}FAST{_RESET} {ts} {outcome_name} "
            f"{event.side} {event.size:.2f} @ {event.price:.3f} "
            f"(${notional:,.2f}) — pending identity"
        )

    def on_book_update(
        self, token_id: str, bids: list[list[float]], asks: list[list[float]]
    ) -> None:
        """Update orderbook state from CLOB WebSocket book snapshots."""
        snap = OrderbookSnapshot(
            token_id=token_id,
            timestamp=int(time.time() * 1000),
            bids=bids,
            asks=asks,
        )
        self._state.latest_books[token_id] = snap

        best_bid = bids[0][0] if bids else None
        best_ask = asks[0][0] if asks else None
        if best_bid is not None and best_ask is not None:
            self._state.current_prices[token_id] = (best_bid + best_ask) / 2
        elif best_bid is not None:
            self._state.current_prices[token_id] = best_bid
        elif best_ask is not None:
            self._state.current_prices[token_id] = best_ask

        self._publish("BOOK_UPDATE", {"token_id": token_id})

    def on_dome_trade(self, trade: TradeEvent) -> None:
        """Handle trade from Dome REST poll (has wallet address).

        Tries to match against pending CLOB events, then scores and alerts.
        """
        matched = self._try_match(trade)
        if matched:
            latency_ms = trade.timestamp * 1000 - matched.timestamp
            logger.info("Matched CLOB event from %dms ago", latency_ms)

        self._score_and_alert(trade)
        self._cleanup_stale()

    def _try_match(self, trade: TradeEvent) -> ClobTradeEvent | None:
        """Match a Dome trade against pending CLOB events.

        Match criteria: same token_id, timestamp within 30s, size within 10%.
        """
        trade_ts_ms = trade.timestamp * 1000
        with self._lock:
            for i, pending in enumerate(self._pending):
                if (
                    pending.token_id == trade.token_id
                    and abs(pending.timestamp - trade_ts_ms) < 30_000
                    and abs(pending.size - trade.size) / max(trade.size, 0.01) < 0.10
                ):
                    self._pending.pop(i)
                    return pending
        return None

    def _cleanup_stale(self) -> None:
        """Remove pending CLOB events older than 60 seconds."""
        cutoff_ms = int(time.time() * 1000) - 60_000
        with self._lock:
            self._pending = [e for e in self._pending if e.timestamp > cutoff_ms]

    def _score_and_alert(self, trade: TradeEvent) -> None:
        """Score a trade and generate alerts if above threshold."""
        # Look up wallet
        profile = self._wallet_cache.get(trade.user_address)
        if profile:
            self._state.wallet_profiles[trade.user_address] = profile

        # Track in trade windows
        if trade.user_address not in self._state.trade_windows:
            self._state.trade_windows[trade.user_address] = []
        self._state.trade_windows[trade.user_address].append(trade)

        # Compute all 6 signals
        freshness = score_freshness(profile)

        book_snap = self._state.latest_books.get(trade.token_id)
        book_dict = None
        if book_snap:
            book_dict = {"bids": book_snap.bids, "asks": book_snap.asks}

        position_size = score_position_size(trade.size, trade.price, book_dict)

        mid_price = self._state.current_prices.get(trade.token_id, trade.price)
        conviction = score_conviction(trade.price, mid_price, trade.side)

        wallet_pos = self._state.wallet_positions.get(trade.user_address, {})
        concentration = score_concentration(wallet_pos)

        # Build coordination window
        window_trades: list[tuple[int, str, float]] = []
        cutoff = trade.timestamp - 1800
        for addr, tw_list in self._state.trade_windows.items():
            for tw in tw_list:
                if tw.token_id == trade.token_id and tw.timestamp >= cutoff:
                    wp = self._state.wallet_profiles.get(addr)
                    f_score = score_freshness(wp)
                    window_trades.append((tw.timestamp, addr, f_score))
        coordination = score_coordination(trade.token_id, window_trades)

        price_before = mid_price
        price_after = self._state.current_prices.get(trade.token_id, trade.price)
        price_impact = score_price_impact(price_before, price_after, trade.side)

        signals = {
            "freshness": freshness,
            "position_size": position_size,
            "conviction": conviction,
            "concentration": concentration,
            "coordination": coordination,
            "price_impact": price_impact,
        }
        result = compute_suspicion(signals)
        composite = result.composite
        signals["composite"] = composite

        # Print trade line and publish event
        print(format_trade_line(trade, trade.outcome_name))
        self._publish(
            "TRADE",
            {
                "token_id": trade.token_id,
                "price": trade.price,
                "size": trade.size,
                "side": trade.side,
                "timestamp": trade.timestamp,
                "user_address": trade.user_address,
                "outcome_name": trade.outcome_name,
            },
        )

        # Alert if above threshold
        if composite >= self._alert_threshold:
            alert_text = format_alert(trade, signals, trade.outcome_name, self._state)
            if self._no_sound:
                alert_text = alert_text.replace("\a", "")
            print(alert_text)

            level = "critical" if composite >= 0.7 else "warning" if composite >= 0.4 else "info"
            alert = Alert(
                level=level,
                title=f"{level.upper()}: {trade.outcome_name}",
                body=f"Score {composite:.2f} — {trade.side} {trade.size:.1f} @ {trade.price:.3f}",
                timestamp=datetime.now(tz=UTC).isoformat(),
                wallet=trade.user_address,
                outcome_name=trade.outcome_name,
            )
            self._state.alerts.append(alert)
            log_alert(alert, _ALERT_LOG_PATH)
            self._publish(
                "ALERT",
                {
                    "level": level,
                    "title": alert.title,
                    "body": alert.body,
                    "timestamp": alert.timestamp,
                    "wallet": trade.user_address,
                    "outcome_name": trade.outcome_name,
                    "score": composite,
                    "side": trade.side,
                    "size": trade.size,
                    "price": trade.price,
                },
            )

            if composite >= 0.7:
                push_alert(alert, self._ntfy_topic)

            # Update watchlist
            reasons = [f"{k}={v:.2f}" for k, v in signals.items() if k != "composite" and v > 0]
            existing = self._state.watchlist.get(trade.user_address)
            if existing is None or composite > existing.score:
                self._state.watchlist[trade.user_address] = WalletSuspicion(
                    address=trade.user_address,
                    score=composite,
                    reasons=reasons,
                    outcome_name=trade.outcome_name,
                    timestamp=datetime.now(tz=UTC).isoformat(),
                )
                self._publish(
                    "WATCHLIST",
                    {
                        "address": trade.user_address,
                        "score": composite,
                    },
                )

    def on_dome_ws_trade(self, data: dict) -> None:
        """Handle real-time trade from Dome WebSocket for watched wallets.

        These are high-priority: the wallet was already flagged as suspicious.
        """
        address = data.get("user_address", "")
        if not address:
            return

        suspicion = self._state.watchlist.get(address)
        if suspicion is None:
            return

        token_id = data.get("token_id", "")
        outcome_name = self._token_to_outcome.get(token_id, "Unknown")
        side = data.get("side", "BUY")
        size = float(data.get("size", 0))
        price = float(data.get("price", 0))
        notional = size * price

        ts = datetime.now(tz=UTC).strftime("%H:%M:%S")
        print(
            f"{_YELLOW}{_BOLD}WATCHED WALLET{_RESET} {ts} {outcome_name} "
            f"{side} {size:.2f} @ {price:.3f} (${notional:,.2f}) "
            f"addr={address[:10]}... score={suspicion.score:.2f}"
        )

        alert = Alert(
            level="critical",
            title=f"WATCHED: {outcome_name}",
            body=f"Watched wallet {address[:10]}... {side} {size:.1f} @ {price:.3f}",
            timestamp=datetime.now(tz=UTC).isoformat(),
            wallet=address,
            outcome_name=outcome_name,
        )
        self._state.alerts.append(alert)
        log_alert(alert, _ALERT_LOG_PATH)
        push_alert(alert, self._ntfy_topic)

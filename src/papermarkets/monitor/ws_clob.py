"""CLOB WebSocket listener for real-time trade detection (~5ms latency).

Connects to Polymarket's CLOB WebSocket, subscribes to all token IDs,
and dispatches trade events and orderbook updates via callbacks.
"""

import json
import logging
import time
from collections.abc import Callable

import websocket

from papermarkets.monitor.state import ClobTradeEvent

logger = logging.getLogger(__name__)

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class ClobWebSocket:
    """CLOB WebSocket listener for real-time trade detection."""

    def __init__(
        self,
        token_ids: list[str],
        on_trade: Callable[[ClobTradeEvent], None],
        on_book: Callable[[str, list, list], None],
    ) -> None:
        self._token_ids = list(token_ids)
        self._on_trade = on_trade
        self._on_book = on_book
        self._ws: websocket.WebSocketApp | None = None
        self._connected = False
        self._should_run = True
        self._msg_count = 0

    @property
    def connected(self) -> bool:
        """Whether the WebSocket is currently connected."""
        return self._connected

    @property
    def token_count(self) -> int:
        """Number of token IDs subscribed to."""
        return len(self._token_ids)

    def stop(self) -> None:
        """Signal the WebSocket to close."""
        self._should_run = False
        if self._ws is not None:
            self._ws.close()

    def connect(self) -> None:
        """Create the WebSocketApp and assign handlers."""
        self._ws = websocket.WebSocketApp(
            WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        """Subscribe to all token IDs on connection."""
        self._connected = True
        sub = {
            "assets_ids": self._token_ids,
            "type": "market",
            "custom_feature_enabled": True,
        }
        ws.send(json.dumps(sub))
        logger.info("CLOB WS connected, subscribed to %d tokens", len(self._token_ids))

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        """Route messages by type."""
        self._msg_count += 1
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("CLOB WS non-JSON message: %s", message[:200])
            return

        if not isinstance(data, dict):
            return

        # Book snapshot: has "bids" and "asks" keys
        if "bids" in data and "asks" in data:
            asset_id = data.get("asset_id", "")
            bids = [[float(b["price"]), float(b["size"])] for b in data["bids"]]
            asks = [[float(a["price"]), float(a["size"])] for a in data["asks"]]
            self._on_book(asset_id, bids, asks)
            return

        event_type = data.get("event_type")

        if event_type == "last_trade_price":
            event = ClobTradeEvent(
                token_id=data.get("asset_id", ""),
                price=float(data.get("price", 0)),
                size=float(data.get("size", 0)),
                side=data.get("side", ""),
                timestamp=int(data.get("timestamp", 0)),
                fee_rate_bps=str(data.get("fee_rate_bps", "0")),
            )
            self._on_trade(event)
            return

        if event_type in ("price_change", "best_bid_ask"):
            logger.debug("CLOB WS %s event (ignored)", event_type)
            return

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        logger.error("CLOB WS error: %s", error)

    def _on_close(
        self,
        ws: websocket.WebSocketApp,
        close_status: int | None,
        close_msg: str | None,
    ) -> None:
        self._connected = False
        logger.info(
            "CLOB WS closed (status=%s, msg=%s, %d msgs received)",
            close_status,
            close_msg,
            self._msg_count,
        )

    def run(self) -> None:
        """Run in a dedicated thread. Auto-reconnect with exponential backoff."""
        backoff = 1.0
        max_backoff = 30.0

        while self._should_run:
            self.connect()
            assert self._ws is not None
            self._ws.run_forever(ping_interval=10, ping_timeout=5)

            if not self._should_run:
                break

            logger.info("CLOB WS reconnecting in %.0fs...", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

            # Reset backoff after a successful connection that lasted > 30s
            if self._msg_count > 0:
                backoff = 1.0
                self._msg_count = 0

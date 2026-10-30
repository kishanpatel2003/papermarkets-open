"""Dome WebSocket listener for real-time copy-trading of suspect wallets.

Connects to Dome's WebSocket feed and subscribes to trades from
specific wallet addresses. Used to monitor HIGH-suspicion wallets
identified by the scoring engine.
"""

import json
import logging
import threading
import time
from collections.abc import Callable

import websocket

logger = logging.getLogger(__name__)


class DomeWebSocket:
    """Dome WebSocket listener for real-time wallet trade monitoring."""

    def __init__(
        self,
        api_key: str,
        on_trade: Callable[[dict], None],
    ) -> None:
        self._api_key = api_key
        self._on_trade = on_trade
        self._ws: websocket.WebSocketApp | None = None
        self._connected = False
        self._should_run = True
        self._msg_count = 0

        # Track active subscriptions for resubscribe on reconnect.
        # Maps subscription_id -> list of wallet addresses.
        self._lock = threading.Lock()
        self._subscriptions: dict[str, list[str]] = {}
        # Pending subscribes awaiting ack: temp_key -> addresses
        self._pending: dict[str, list[str]] = {}
        self._pending_counter = 0

    @property
    def connected(self) -> bool:
        """Whether the WebSocket is currently connected."""
        return self._connected

    @property
    def watched_count(self) -> int:
        """Number of unique wallet addresses being watched."""
        with self._lock:
            addresses: set[str] = set()
            for addrs in self._subscriptions.values():
                addresses.update(addrs)
            return len(addresses)

    def watch_wallets(self, addresses: list[str]) -> None:
        """Subscribe to real-time trades for specific wallets."""
        if not self._connected or self._ws is None:
            logger.warning("Dome WS not connected, cannot subscribe")
            return

        sub_msg = {
            "action": "subscribe",
            "platform": "polymarket",
            "version": 1,
            "type": "orders",
            "filters": {"users": addresses},
        }

        with self._lock:
            self._pending_counter += 1
            pending_key = f"pending_{self._pending_counter}"
            self._pending[pending_key] = list(addresses)

        self._ws.send(json.dumps(sub_msg))
        logger.info("Dome WS subscribing to %d wallets", len(addresses))

    def unwatch_wallets(self, subscription_id: str) -> None:
        """Unsubscribe from a wallet watch."""
        if not self._connected or self._ws is None:
            logger.warning("Dome WS not connected, cannot unsubscribe")
            return

        unsub_msg = {
            "action": "unsubscribe",
            "subscription_id": subscription_id,
        }
        self._ws.send(json.dumps(unsub_msg))

        with self._lock:
            self._subscriptions.pop(subscription_id, None)

        logger.info("Dome WS unsubscribed %s", subscription_id)

    def stop(self) -> None:
        """Signal the WebSocket to close."""
        self._should_run = False
        if self._ws is not None:
            self._ws.close()

    def _ws_url(self) -> str:
        return f"wss://ws.domeapi.io/{self._api_key}"

    def _connect(self) -> None:
        """Create the WebSocketApp and assign handlers."""
        self._ws = websocket.WebSocketApp(
            self._ws_url(),
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        self._connected = True
        logger.info("Dome WS connected")
        self._resubscribe_all()

    def _resubscribe_all(self) -> None:
        """Re-send all active subscriptions after reconnect.

        Dome does NOT restore subscriptions after reconnect, so we must
        re-subscribe to all previously watched wallet sets.
        """
        with self._lock:
            old_subs = dict(self._subscriptions)
            self._subscriptions.clear()

        for addresses in old_subs.values():
            self.watch_wallets(addresses)

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        self._msg_count += 1
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("Dome WS non-JSON message: %s", message[:200])
            return

        if not isinstance(data, dict):
            return

        msg_type = data.get("type")

        if msg_type == "ack":
            sub_id = data.get("subscription_id", "")
            with self._lock:
                # Associate the ack with the oldest pending subscribe
                if self._pending:
                    oldest_key = next(iter(self._pending))
                    addresses = self._pending.pop(oldest_key)
                    self._subscriptions[sub_id] = addresses
                    logger.info(
                        "Dome WS subscription acked: %s (%d wallets)",
                        sub_id,
                        len(addresses),
                    )
                else:
                    logger.info("Dome WS ack for %s (no pending)", sub_id)
            return

        if msg_type == "event":
            self._on_trade(data)
            return

        logger.debug("Dome WS unknown message type: %s", msg_type)

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        logger.error("Dome WS error: %s", error)

    def _on_close(
        self,
        ws: websocket.WebSocketApp,
        close_status: int | None,
        close_msg: str | None,
    ) -> None:
        self._connected = False
        logger.info(
            "Dome WS closed (status=%s, msg=%s, %d msgs received)",
            close_status,
            close_msg,
            self._msg_count,
        )

    def run(self) -> None:
        """Run in a dedicated thread. Auto-reconnect + resubscribe."""
        backoff = 1.0
        max_backoff = 30.0

        while self._should_run:
            self._connect()
            assert self._ws is not None
            self._ws.run_forever(ping_interval=10, ping_timeout=5)

            if not self._should_run:
                break

            logger.info("Dome WS reconnecting in %.0fs...", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

            if self._msg_count > 0:
                backoff = 1.0
                self._msg_count = 0

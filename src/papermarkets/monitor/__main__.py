"""Entry point for the ZachXBT insider-trading monitor.

Usage:
    python -m papermarkets.monitor --event-slug <slug> [options]
"""

import argparse
import json
import logging
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from papermarkets.ingest.config import DOME_API_KEY
from papermarkets.monitor.config import (
    EventConfig,
    Outcome,
    fetch_event_config,
    load_event_config,
)
from papermarkets.monitor.correlator import TradeCorrelator
from papermarkets.monitor.display import (
    print_dashboard,
    print_watchlist,
)
from papermarkets.monitor.poller import poll_orderbooks, poll_trades
from papermarkets.monitor.state import MonitorState
from papermarkets.monitor.wallet import WalletCache
from papermarkets.monitor.ws_clob import ClobWebSocket
from papermarkets.monitor.ws_dome import DomeWebSocket

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]
_STATE_PATH = _ROOT / "data" / "monitor" / "state.json"
_ALERT_LOG_PATH = _ROOT / "data" / "monitor" / "alerts.jsonl"
_TRADES_DUMP_PATH = _ROOT / "data" / "monitor" / "trades_dump.jsonl"

# ANSI
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_CYAN = "\033[96m"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor a Polymarket event for insider-trading signals.",
    )
    parser.add_argument("--event-slug", required=True, help="Polymarket event slug")
    parser.add_argument(
        "--trade-interval", type=int, default=15, help="Seconds between trade polls (default: 15)"
    )
    parser.add_argument(
        "--book-interval", type=int, default=60, help="Seconds between book polls (default: 60)"
    )
    parser.add_argument(
        "--alert-threshold",
        type=float,
        default=0.2,
        help="Suspicion score threshold for alerts (default: 0.2)",
    )
    parser.add_argument("--no-sound", action="store_true", help="Disable terminal bell on alerts")
    parser.add_argument(
        "--ntfy-topic",
        type=str,
        default="",
        help="ntfy.sh topic for mobile push notifications on HIGH alerts",
    )
    parser.add_argument(
        "--dashboard-interval",
        type=int,
        default=300,
        help="Seconds between dashboard prints (default: 300)",
    )
    parser.add_argument(
        "--no-websocket",
        action="store_true",
        help="Disable WebSocket pipelines, use REST-only mode",
    )
    parser.add_argument(
        "--ui",
        type=str,
        choices=["raw", "tui", "web"],
        default="raw",
        help="UI mode: raw (terminal), tui (Textual), web (localhost:8080)",
    )
    return parser.parse_args()


def _load_config(event_slug: str) -> EventConfig:
    """Try local JSON first, fall back to Gamma API."""
    default_path = _ROOT / "data" / "monitor" / "zachxbt_event.json"
    if default_path.exists():
        logger.info("Loading event config from %s", default_path)
        return load_event_config(default_path)
    logger.info("Fetching event config from Gamma API for '%s'", event_slug)
    return fetch_event_config(event_slug)


def _print_banner(
    config: EventConfig,
    outcomes: list[Outcome],
    args: argparse.Namespace,
    clob_ws: ClobWebSocket | None = None,
    dome_ws: DomeWebSocket | None = None,
) -> None:
    token_count = sum(1 for o in outcomes for t in (o.yes_token_id, o.no_token_id) if t)
    print()
    print(f"{_BOLD}{_CYAN}{'=' * 72}{_RESET}")
    print(f"{_BOLD}{_CYAN}  ZACHXBT INSIDER TRADING MONITOR{_RESET}")
    print(f"{_CYAN}{'=' * 72}{_RESET}")
    print(f"  Event:      {config.event_title}")
    print(f"  Outcomes:   {len(outcomes)} active (of {len(config.outcomes)} total)")
    print(f"  Tokens:     {token_count}")
    ti, bi, di = args.trade_interval, args.book_interval, args.dashboard_interval
    print(f"  Intervals:  trades={ti}s  books={bi}s  dashboard={di}s")
    print(f"  Threshold:  {args.alert_threshold}")
    print(f"  Sound:      {'off' if args.no_sound else 'on'}")
    ntfy = args.ntfy_topic if args.ntfy_topic else "off"
    print(f"  Push:       {ntfy}")

    if clob_ws is not None and dome_ws is not None:
        print("  Mode:       DUAL PIPELINE (CLOB WS + Dome REST + Dome WS)")
        print(f"  CLOB WS:    connecting... ({clob_ws.token_count} tokens)")
        print(f"  Dome WS:    connecting... (watching {dome_ws.watched_count} wallets)")
        print(f"  Dome REST:  polling every {ti}s")
    else:
        print("  Mode:       REST ONLY (Dome REST polling)")

    print(f"{_CYAN}{'=' * 72}{_RESET}")
    print()


def _backfill(
    outcomes: list[Outcome],
    state: MonitorState,
    correlator: TradeCorrelator,
    config: EventConfig,
) -> None:
    """Fetch all trades since market creation."""
    print(f"{_BOLD}Backfilling trades since market creation...{_RESET}")

    # Set last_trade_ts to market creation time for initial fetch
    created_ts = 0
    if config.created_at:
        try:
            dt = datetime.fromisoformat(config.created_at)
            created_ts = int(dt.timestamp())
        except (ValueError, TypeError):
            pass
    if created_ts == 0:
        created_ts = int(time.time()) - 86400 * 30  # fallback: 30 days ago

    for outcome in outcomes:
        for token_id in (outcome.yes_token_id, outcome.no_token_id):
            if token_id and token_id not in state.last_trade_ts:
                state.last_trade_ts[token_id] = created_ts

    trades = poll_trades(outcomes, state)
    print(f"  Backfilled {len(trades)} trades")

    for trade in trades:
        correlator.on_dome_trade(trade)

    print()


def _dump_trades(state: MonitorState) -> None:
    """Dump all accumulated trades to JSONL."""
    _TRADES_DUMP_PATH.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(_TRADES_DUMP_PATH, "w") as f:
        for _addr, trades in state.trade_windows.items():
            for t in trades:
                record = {
                    "trade_id": t.trade_id,
                    "market_id": t.market_id,
                    "token_id": t.token_id,
                    "token_label": t.token_label,
                    "side": t.side,
                    "price": t.price,
                    "size": t.size,
                    "timestamp": t.timestamp,
                    "user_address": t.user_address,
                    "taker_address": t.taker_address,
                    "tx_hash": t.tx_hash,
                    "block_number": t.block_number,
                    "outcome_name": t.outcome_name,
                }
                f.write(json.dumps(record) + "\n")
                count += 1
    if count > 0:
        logger.info("Dumped %d trades to %s", count, _TRADES_DUMP_PATH)


def main() -> None:
    args = _parse_args()

    config = _load_config(args.event_slug)
    outcomes = config.active_outcomes
    if not outcomes:
        print("No active outcomes with volume. Exiting.")
        sys.exit(1)

    state = MonitorState.load_state(_STATE_PATH)
    wallet_cache = WalletCache()
    wallet_cache.preload(state.wallet_profiles)

    # Event bus for UI layers
    from papermarkets.monitor.events import EventBus

    event_bus = EventBus()

    # Create correlator (used in both modes)
    correlator = TradeCorrelator(
        state=state,
        wallet_cache=wallet_cache,
        outcomes=outcomes,
        alert_threshold=args.alert_threshold,
        no_sound=args.no_sound,
        ntfy_topic=args.ntfy_topic,
        event_bus=event_bus,
    )

    clob_ws = None
    dome_ws = None

    if not args.no_websocket:
        # Collect all token IDs
        token_ids = [tid for o in outcomes for tid in (o.yes_token_id, o.no_token_id) if tid]

        # Start CLOB WebSocket
        clob_ws = ClobWebSocket(
            token_ids=token_ids,
            on_trade=correlator.on_clob_trade,
            on_book=correlator.on_book_update,
        )
        clob_thread = threading.Thread(target=clob_ws.run, daemon=True, name="clob-ws")
        clob_thread.start()

        # Start Dome WebSocket
        dome_ws = DomeWebSocket(
            api_key=DOME_API_KEY,
            on_trade=correlator.on_dome_ws_trade,
        )
        dome_thread = threading.Thread(target=dome_ws.run, daemon=True, name="dome-ws")
        dome_thread.start()

    # TUI mode: launch Textual app (takes over the terminal)
    if args.ui == "tui":
        from papermarkets.monitor.tui import MonitorApp

        # Backfill and start orderbook fetch before TUI takes over
        _backfill(outcomes, state, correlator, config)
        poll_orderbooks(outcomes, state)

        # Start the polling loop in a background thread
        def _poll_loop() -> None:
            dome_ws_watched: set[str] = set()
            last_trade_poll = time.time()
            last_book_poll = time.time()
            last_save = time.time()
            while True:
                now = time.time()
                if now - last_trade_poll >= args.trade_interval:
                    last_trade_poll = now
                    try:
                        new_trades = poll_trades(outcomes, state)
                        for trade in new_trades:
                            correlator.on_dome_trade(trade)
                    except Exception:
                        logger.exception("Error during trade poll")
                    if dome_ws and dome_ws.connected:
                        for addr, sus in state.watchlist.items():
                            if sus.score >= 0.7 and addr not in dome_ws_watched:
                                dome_ws_watched.add(addr)
                                dome_ws.watch_wallets([addr])
                if args.no_websocket and now - last_book_poll >= args.book_interval:
                    last_book_poll = now
                    try:
                        poll_orderbooks(outcomes, state)
                    except Exception:
                        logger.exception("Error during orderbook poll")
                if now - last_save >= 300:
                    last_save = now
                    state.save_state(_STATE_PATH)
                time.sleep(1)

        poll_thread = threading.Thread(target=_poll_loop, daemon=True, name="poll-loop")
        poll_thread.start()

        tui_app = MonitorApp(state=state, config=config, event_bus=event_bus)
        tui_app.run()

        # TUI exited — save state and clean up
        if clob_ws:
            clob_ws.stop()
        if dome_ws:
            dome_ws.stop()
        state.save_state(_STATE_PATH)
        _dump_trades(state)
        return

    # Web mode: start FastAPI in a background thread, run polling in foreground
    if args.ui == "web":
        import uvicorn

        from papermarkets.monitor.web import init_web

        web_app = init_web(state, config, event_bus)

        def _run_web() -> None:
            uvicorn.run(web_app, host="0.0.0.0", port=8080, log_level="warning")

        web_thread = threading.Thread(target=_run_web, daemon=True, name="web-server")
        web_thread.start()
        logger.info("Web dashboard running at http://localhost:8080")

    # Raw / web mode: print banner and run polling loop in foreground
    _print_banner(config, outcomes, args, clob_ws, dome_ws)

    # Graceful shutdown
    shutting_down = False

    def _shutdown(signum, frame):
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        print(f"\n{_BOLD}Shutting down...{_RESET}")
        if clob_ws:
            clob_ws.stop()
        if dome_ws:
            dome_ws.stop()
        state.save_state(_STATE_PATH)
        print_dashboard(state, config)
        print_watchlist(state)
        _dump_trades(state)
        print(f"{_DIM}State saved. Goodbye.{_RESET}")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Backfill
    _backfill(outcomes, state, correlator, config)

    # Initial orderbook fetch
    print(f"{_BOLD}Fetching initial orderbooks...{_RESET}")
    poll_orderbooks(outcomes, state)
    print()

    # Track wallets already subscribed to Dome WS
    dome_ws_watched: set[str] = set()

    # Main loop
    last_trade_poll = time.time()
    last_book_poll = time.time()
    last_dashboard = time.time()
    last_save = time.time()

    print(f"{_BOLD}Entering live monitoring loop.{_RESET}")
    print(f"{_DIM}Press Ctrl+C to stop.{_RESET}\n")

    while True:
        now = time.time()

        if now - last_trade_poll >= args.trade_interval:
            last_trade_poll = now
            try:
                new_trades = poll_trades(outcomes, state)
                for trade in new_trades:
                    correlator.on_dome_trade(trade)
            except Exception:
                logger.exception("Error during trade poll")

            # Auto-subscribe HIGH wallets to Dome WS
            if dome_ws and dome_ws.connected:
                for addr, sus in state.watchlist.items():
                    if sus.score >= 0.7 and addr not in dome_ws_watched:
                        dome_ws_watched.add(addr)
                        dome_ws.watch_wallets([addr])
                        logger.info("Auto-subscribed HIGH wallet %s to Dome WS", addr[:10])

        # Only poll orderbooks via REST if WebSockets are disabled
        if args.no_websocket and now - last_book_poll >= args.book_interval:
            last_book_poll = now
            try:
                poll_orderbooks(outcomes, state)
            except Exception:
                logger.exception("Error during orderbook poll")

        if now - last_dashboard >= args.dashboard_interval:
            last_dashboard = now
            print_dashboard(state, config)

        if now - last_save >= 300:
            last_save = now
            state.save_state(_STATE_PATH)

        time.sleep(1)


if __name__ == "__main__":
    main()

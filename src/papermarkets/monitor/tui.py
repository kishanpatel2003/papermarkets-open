"""Terminal UI for the insider trading monitor, built with Textual."""

import contextlib
from datetime import UTC, datetime

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from papermarkets.monitor.config import EventConfig
from papermarkets.monitor.events import EventBus, EventType, MonitorEvent
from papermarkets.monitor.state import MonitorState

MAX_FEED_LINES = 200


def _truncate_address(addr: str) -> str:
    if len(addr) > 10:
        return f"{addr[:6]}...{addr[-4:]}"
    return addr


class StatusBar(Static):
    """Connection status indicators at the top."""

    def __init__(self, state: MonitorState, config: EventConfig, **kwargs) -> None:
        super().__init__(**kwargs)
        self._state = state
        self._config = config
        self._clob_status = "disconnected"
        self._dome_rest_status = "polling"
        self._dome_ws_status = "disconnected"
        self._ws_wallets = 0
        self._clob_tokens = 0
        self._start_time = datetime.now(tz=UTC)

    def on_mount(self) -> None:
        self._clob_tokens = sum(
            1 for o in self._config.active_outcomes for t in (o.yes_token_id, o.no_token_id) if t
        )
        self._update_text()
        self.set_interval(10, self._update_text)

    def _update_text(self) -> None:
        elapsed = datetime.now(tz=UTC) - self._start_time
        hours = int(elapsed.total_seconds()) // 3600
        minutes = (int(elapsed.total_seconds()) % 3600) // 60
        clob_dot = "[green]●[/]" if self._clob_status == "connected" else "[red]●[/]"
        dome_ws_dot = "[green]●[/]" if self._dome_ws_status == "connected" else "[red]●[/]"
        self.update(
            f"[bold]ZACHXBT INSIDER TRADING MONITOR[/]    DUAL PIPELINE MODE\n"
            f"CLOB WS: {clob_dot} {self._clob_status} ({self._clob_tokens} tokens)  "
            f"Dome REST: {self._dome_rest_status}  "
            f"Dome WS: {dome_ws_dot} {self._dome_ws_status} ({self._ws_wallets} wallets)  "
            f"Uptime: {hours}h {minutes:02d}m"
        )

    def update_status(self, source: str, status: str, **kwargs: int) -> None:
        if source == "clob_ws":
            self._clob_status = status
        elif source == "dome_rest":
            self._dome_rest_status = status
        elif source == "dome_ws":
            self._dome_ws_status = status
            self._ws_wallets = kwargs.get("wallets", self._ws_wallets)
        self._update_text()


class TradeFeed(RichLog):
    """Scrolling trade feed panel."""

    def __init__(self) -> None:
        super().__init__(max_lines=MAX_FEED_LINES, wrap=True, markup=True)


class AlertPanel(RichLog):
    """Scrolling alerts panel."""

    def __init__(self) -> None:
        super().__init__(max_lines=MAX_FEED_LINES, wrap=True, markup=True)


class MonitorApp(App):
    """Textual TUI for the ZachXBT insider trading monitor."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #status-bar {
        height: 3;
        padding: 0 1;
        background: $surface;
        border-bottom: solid $accent;
    }
    #main-split {
        height: 1fr;
    }
    #trade-feed {
        width: 2fr;
        border-right: solid $accent;
    }
    #alert-panel {
        width: 1fr;
    }
    #dashboard-table {
        height: auto;
        max-height: 14;
        border-top: solid $accent;
    }
    #watchlist-table {
        height: auto;
        max-height: 10;
        border-top: solid $accent;
    }
    .panel-title {
        height: 1;
        padding: 0 1;
        background: $accent;
        color: $text;
        text-style: bold;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("d", "toggle_dashboard", "Dashboard"),
    ]

    def __init__(
        self,
        state: MonitorState,
        config: EventConfig,
        event_bus: EventBus,
    ) -> None:
        super().__init__()
        self._state = state
        self._config = config
        self._event_bus = event_bus

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatusBar(self._state, self._config, id="status-bar")
        with Horizontal(id="main-split"):
            with Vertical(id="trade-feed"):
                yield Static("LIVE TRADE FEED", classes="panel-title")
                yield TradeFeed()
            with Vertical(id="alert-panel"):
                yield Static("ALERTS", classes="panel-title")
                yield AlertPanel()
        yield DataTable(id="dashboard-table")
        yield DataTable(id="watchlist-table")
        yield Footer()

    def on_mount(self) -> None:
        # Set up dashboard table
        dash = self.query_one("#dashboard-table", DataTable)
        dash.add_columns("Outcome", "Price", "Volume", "HIGH", "MED", "LOW", "Top Suspect")
        self._refresh_dashboard()

        # Set up watchlist table
        wl = self.query_one("#watchlist-table", DataTable)
        wl.add_columns("Wallet", "Score", "Outcome", "Reasons")
        self._refresh_watchlist()

        # Subscribe to events
        self._event_bus.subscribe(EventType.TRADE, self._on_trade)
        self._event_bus.subscribe(EventType.FAST_TRADE, self._on_fast_trade)
        self._event_bus.subscribe(EventType.ALERT, self._on_alert)
        self._event_bus.subscribe(EventType.STATUS, self._on_status)
        self._event_bus.subscribe(EventType.WATCHLIST, self._on_watchlist)
        self._event_bus.subscribe(EventType.BOOK_UPDATE, self._on_book_update)

        # Periodic refresh of tables
        self.set_interval(30, self._refresh_dashboard)
        self.set_interval(30, self._refresh_watchlist)

    def _on_trade(self, event: MonitorEvent) -> None:
        d = event.data
        ts = datetime.fromtimestamp(d["timestamp"], tz=UTC).strftime("%H:%M:%S")
        addr = _truncate_address(d.get("user_address", ""))
        dollar = d["size"] * d["price"]
        side = d["side"].upper()
        side_markup = f"[red]{side}[/]" if side == "SELL" else f"[green]{side}[/]"
        line = (
            f"[dim]{ts}[/] {d.get('outcome_name', ''):<20} "
            f"{side_markup:>4} {d['size']:>10.2f} @ {d['price']:.3f} "
            f"(${dollar:>9,.2f}) {addr}"
        )
        self.call_from_thread(self.query_one(TradeFeed).write, line)
        self.call_from_thread(self._refresh_dashboard)

    def _on_fast_trade(self, event: MonitorEvent) -> None:
        d = event.data
        ts = datetime.fromtimestamp(d["timestamp"] / 1000, tz=UTC).strftime("%H:%M:%S")
        side = d["side"].upper()
        side_markup = f"[red]{side}[/]" if side == "SELL" else f"[green]{side}[/]"
        dollar = d["size"] * d["price"]
        line = (
            f"[dim]{ts}[/] [bold yellow]CLOB[/] "
            f"{side_markup:>4} {d['size']:>10.2f} @ {d['price']:.3f} "
            f"(${dollar:>9,.2f})"
        )
        self.call_from_thread(self.query_one(TradeFeed).write, line)

    def _on_alert(self, event: MonitorEvent) -> None:
        d = event.data
        level = d.get("level", "info")
        ts = d.get("timestamp", "")
        if ts:
            with contextlib.suppress(ValueError, TypeError):
                ts = datetime.fromisoformat(ts).strftime("%H:%M:%S")

        if level == "critical":
            prefix = "[bold red]!! HIGH[/]"
        elif level == "warning":
            prefix = "[bold yellow]!  MED[/]"
        else:
            prefix = "[dim].  LOW[/]"

        panel = self.query_one(AlertPanel)
        self.call_from_thread(
            panel.write,
            f"{prefix} {ts}  Score: {d.get('score', 0):.2f}\n"
            f"  {d.get('outcome_name', '')} {d.get('side', '')} "
            f"{d.get('size', 0):.0f} @ {d.get('price', 0):.3f}\n"
            f"  {_truncate_address(d.get('wallet', ''))}\n",
        )

    def _on_fast_trade_alert(self, event: MonitorEvent) -> None:
        d = event.data
        ts = datetime.fromtimestamp(d["timestamp"] / 1000, tz=UTC).strftime("%H:%M:%S")
        dollar = d["size"] * d["price"]
        panel = self.query_one(AlertPanel)
        self.call_from_thread(
            panel.write,
            f"[bold yellow]\\[lightning] FAST[/] {ts}\n"
            f"  {d.get('outcome_name', '')} {d['side'].upper()} "
            f"{d['size']:.0f} @ {d['price']:.3f} (${dollar:,.2f})\n"
            f"  pending identity...\n",
        )

    def _on_status(self, event: MonitorEvent) -> None:
        d = event.data
        bar = self.query_one(StatusBar)
        self.call_from_thread(
            bar.update_status,
            d.get("source", ""),
            d.get("status", ""),
            wallets=d.get("wallets", 0),
        )

    def _on_watchlist(self, _event: MonitorEvent) -> None:
        self.call_from_thread(self._refresh_watchlist)

    def _on_book_update(self, _event: MonitorEvent) -> None:
        self.call_from_thread(self._refresh_dashboard)

    def _refresh_dashboard(self) -> None:
        table = self.query_one("#dashboard-table", DataTable)
        table.clear()
        outcomes = sorted(self._config.active_outcomes, key=lambda o: o.volume, reverse=True)
        for o in outcomes:
            price = self._state.current_prices.get(o.yes_token_id, o.yes_price or 0)
            high = med = low = 0
            for a in self._state.alerts:
                if a.outcome_name == o.name:
                    if a.level == "critical":
                        high += 1
                    elif a.level == "warning":
                        med += 1
                    else:
                        low += 1
            top_wallet = ""
            top_score = 0.0
            for addr, sus in self._state.watchlist.items():
                if sus.outcome_name == o.name and sus.score > top_score:
                    top_score = sus.score
                    top_wallet = _truncate_address(addr)
            suspect = f"{top_wallet} ({top_score:.2f})" if top_wallet else "-"
            table.add_row(
                o.name,
                f"{price:.3f}",
                f"{o.volume:,.0f}",
                str(high),
                str(med),
                str(low),
                suspect,
            )

    def _refresh_watchlist(self) -> None:
        table = self.query_one("#watchlist-table", DataTable)
        table.clear()
        entries = sorted(self._state.watchlist.values(), key=lambda s: s.score, reverse=True)
        for sus in entries:
            addr = _truncate_address(sus.address)
            reasons = ", ".join(sus.reasons[:3])
            table.add_row(addr, f"{sus.score:.2f}", sus.outcome_name, reasons)

    def action_toggle_dashboard(self) -> None:
        dash = self.query_one("#dashboard-table", DataTable)
        dash.display = not dash.display

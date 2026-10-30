"""Dashboard and analysis output for the terminal."""

from datetime import UTC, datetime

from papermarkets.monitor.config import EventConfig
from papermarkets.monitor.state import MonitorState, TradeEvent

# ANSI escape codes
_RED = "\033[91m"
_YELLOW = "\033[93m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _truncate_address(addr: str) -> str:
    if len(addr) > 10:
        return f"{addr[:6]}...{addr[-4:]}"
    return addr


def _box_top(width: int) -> str:
    return "╔" + "═" * width + "╗"


def _box_bottom(width: int) -> str:
    return "╚" + "═" * width + "╝"


def _box_sep(width: int) -> str:
    return "│" + "─" * width + "│"


def print_dashboard(state: MonitorState, config: EventConfig) -> None:
    """Print a periodic summary table of all active outcomes."""
    outcomes = config.active_outcomes
    if not outcomes:
        print(f"{_DIM}No active outcomes with volume.{_RESET}")
        return

    outcomes = sorted(outcomes, key=lambda o: o.volume, reverse=True)
    now = datetime.now(tz=UTC).strftime("%H:%M:%S")

    w = 88
    print(_box_top(w))
    print(f"│{_BOLD}  DASHBOARD  {now:<{w - 13}}{_RESET}│")
    print(_box_sep(w))

    # Header
    print(
        f"│ {'Outcome':<24} {'Price':>6} {'Volume':>12}"
        f" {'HIGH':>5} {'MED':>5} {'LOW':>5}"
        f" {'Top Suspect':<20} │"
    )
    print(_box_sep(w))

    for o in outcomes:
        price = state.current_prices.get(o.yes_token_id, o.yes_price or 0)

        # Count alerts by tier for this outcome
        high = med = low = 0
        for a in state.alerts:
            if a.outcome_name == o.name:
                if a.level == "critical":
                    high += 1
                elif a.level == "warning":
                    med += 1
                else:
                    low += 1

        # Find top suspicious wallet for this outcome
        top_wallet = ""
        top_score = 0.0
        for addr, sus in state.watchlist.items():
            if sus.outcome_name == o.name and sus.score > top_score:
                top_score = sus.score
                top_wallet = _truncate_address(addr)

        suspect_display = f"{top_wallet} ({top_score:.2f})" if top_wallet else "—"

        print(
            f"│ {o.name:<24} {price:>6.3f} {o.volume:>12,.0f}"
            f" {high:>5} {med:>5} {low:>5}"
            f" {suspect_display:<20} │"
        )

    print(_box_bottom(w))
    print()


def print_watchlist(state: MonitorState) -> None:
    """Print the current watchlist sorted by composite score descending."""
    if not state.watchlist:
        print(f"{_DIM}Watchlist empty.{_RESET}")
        return

    entries = sorted(state.watchlist.values(), key=lambda s: s.score, reverse=True)

    w = 78
    print(_box_top(w))
    print(f"│{_BOLD}  WATCHLIST{' ' * (w - 10)}{_RESET}│")
    print(_box_sep(w))
    print(f"│ {'Wallet':<16} {'Freshness':>9} {'Volume':>12} {'Outcome':<20} {'Score':>8} │")
    print(_box_sep(w))

    for sus in entries:
        addr = _truncate_address(sus.address)
        # Compute volume from wallet_positions if available
        positions = state.wallet_positions.get(sus.address, {})
        volume = sum(abs(v) for v in positions.values())

        # Freshness: how recently the suspicion was recorded
        freshness = "—"
        if sus.timestamp:
            try:
                sus_dt = datetime.fromisoformat(sus.timestamp)
                delta = datetime.now(tz=UTC) - sus_dt
                mins = int(delta.total_seconds() / 60)
                freshness = f"{mins}m ago" if mins < 60 else f"{mins // 60}h ago"
            except (ValueError, TypeError):
                pass

        color = _RED if sus.score >= 0.7 else _YELLOW if sus.score >= 0.4 else _DIM
        print(
            f"│ {color}{addr:<16} {freshness:>9} {volume:>12,.0f}"
            f" {sus.outcome_name:<20} {sus.score:>8.3f}{_RESET} │"
        )

    print(_box_bottom(w))
    print()


def print_copy_analysis(
    outcome_name: str,
    state: MonitorState,
    config: EventConfig,
) -> None:
    """Print copy-trade analysis for an outcome."""
    # Find the matching outcome
    outcome = None
    for o in config.outcomes:
        if o.name == outcome_name:
            outcome = o
            break

    if outcome is None:
        print(f"{_DIM}Outcome '{outcome_name}' not found.{_RESET}")
        return

    yes_price = state.current_prices.get(outcome.yes_token_id, outcome.yes_price or 0)

    # Gather suspicious wallets for this outcome
    suspicious = [s for s in state.watchlist.values() if s.outcome_name == outcome_name]
    suspicious.sort(key=lambda s: s.score, reverse=True)

    w = 68
    print(_box_top(w))
    print(f"│{_BOLD}  COPY ANALYSIS: {outcome_name:<{w - 17}}{_RESET}│")
    print(_box_sep(w))
    print(f"│  Current Yes Price: {yes_price:.3f}{' ' * (w - 23 - len(f'{yes_price:.3f}'))}│")
    print(f"│  Suspicious Wallets: {len(suspicious)}{' ' * (w - 24 - len(str(len(suspicious))))}│")

    if suspicious:
        # Average entry price: estimate from positions
        entry_prices = []
        for sus in suspicious:
            positions = state.wallet_positions.get(sus.address, {})
            pos = positions.get(outcome.yes_token_id, 0)
            if pos > 0:
                entry_prices.append(pos)

        if entry_prices:
            avg_entry = sum(entry_prices) / len(entry_prices)
            pnl = (yes_price - avg_entry) / avg_entry * 100 if avg_entry > 0 else 0
            avg_str = f"{avg_entry:.3f}"
            pad = w - 23 - len(avg_str)
            print(f"│  Avg Entry Price:   {avg_str}{' ' * pad}│")
            print(f"│  Current PnL:       {pnl:+.1f}%{' ' * (w - 23 - len(f'{pnl:+.1f}%'))}│")

        # Time remaining
        if outcome.active and config.end_date:
            try:
                end = datetime.fromisoformat(config.end_date)
                now = datetime.now(tz=UTC)
                remaining = end - now
                days = remaining.days
                hours = remaining.seconds // 3600
                time_str = f"{days}d {hours}h" if days > 0 else f"{hours}h"
                print(f"│  Time Remaining:    {time_str:<{w - 22}}│")
            except (ValueError, TypeError):
                pass

        print(_box_sep(w))
        print(f"│  Hypothetical Buy Scenarios:{' ' * (w - 29)}│")
        for amount in [100, 500, 1000]:
            if yes_price > 0 and yes_price < 1:
                shares = amount / yes_price
                profit_if_yes = shares - amount
                print(
                    f"│    ${amount:>5} -> {shares:>8.1f} shares"
                    f"  (profit if Yes: ${profit_if_yes:>8,.0f})"
                    f"{' ' * max(0, w - 55)}│"
                )

    print(_box_bottom(w))
    print()


def format_trade_line(trade: TradeEvent, outcome_name: str) -> str:
    """Compact one-line trade summary for the live feed."""
    ts = datetime.fromtimestamp(trade.timestamp, tz=UTC).strftime("%H:%M:%S")
    addr = _truncate_address(trade.user_address)
    dollar = trade.size * trade.price
    side_color = _RED if trade.side.upper() == "SELL" else ""
    reset = _RESET if side_color else ""
    return (
        f"{_DIM}{ts}{_RESET} {outcome_name:<20} "
        f"{side_color}{trade.side.upper():>4}{reset} "
        f"{trade.size:>10.2f} @ {trade.price:.3f} "
        f"(${dollar:>9,.2f}) {addr}"
    )

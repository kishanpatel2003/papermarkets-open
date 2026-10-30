"""Alert formatting and persistent logging."""

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from papermarkets.monitor.state import Alert, MonitorState, TradeEvent

logger = logging.getLogger(__name__)

# ntfy push notifications — set NTFY_TOPIC env var or pass --ntfy-topic
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

# ANSI escape codes
_RED = "\033[91m"
_YELLOW = "\033[93m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"
_BG_RED = "\033[41m"
_BELL = "\a"


def _truncate_address(addr: str) -> str:
    if len(addr) > 10:
        return f"{addr[:6]}...{addr[-4:]}"
    return addr


def _level_from_score(score: float) -> str:
    if score >= 0.7:
        return "HIGH"
    if score >= 0.4:
        return "MEDIUM"
    return "LOW"


def format_alert(
    trade: TradeEvent,
    scores: dict[str, float],
    outcome_name: str,
    state: MonitorState,
) -> str:
    """Format a single trade alert with ANSI colors for terminal output."""
    composite = scores.get("composite", 0.0)
    level = _level_from_score(composite)

    # Color scheme per level
    if level == "HIGH":
        prefix = f"{_BELL}{_BG_RED}{_BOLD} !! HIGH "
        color = _BG_RED + _BOLD
    elif level == "MEDIUM":
        prefix = f"{_YELLOW}{_BOLD} !  MED  "
        color = _YELLOW
    else:
        prefix = f"{_DIM} .  LOW  "
        color = _DIM

    ts = datetime.fromtimestamp(trade.timestamp, tz=UTC).strftime("%H:%M:%S")
    addr = _truncate_address(trade.user_address)
    dollar_value = trade.size * trade.price

    # Header line
    lines = [
        f"{prefix}│ {composite:.2f} │ {ts} │ {outcome_name}{_RESET}",
        f"{color}  Price: {trade.price:.3f}  Wallet: {addr}{_RESET}",
        f"{color}  {trade.side.upper():>4} {trade.size:>10.2f} @ {trade.price:.3f}"
        f"  (${dollar_value:,.2f}){_RESET}",
    ]

    # Individual signal scores
    signal_parts = []
    for key, val in sorted(scores.items()):
        if key != "composite":
            signal_parts.append(f"{key}={val:.2f}")
    if signal_parts:
        lines.append(f"{color}  Signals: {', '.join(signal_parts)}{_RESET}")

    # Tx hash and Dome link
    lines.append(f"{color}  Tx: {trade.tx_hash}{_RESET}")
    dome_url = f"https://domeapi.io/wallet/{trade.user_address}"
    lines.append(f"{color}  Dome: {dome_url}{_RESET}")
    lines.append("")

    return "\n".join(lines)


def log_alert(alert: Alert, path: Path) -> None:
    """Append a single alert as a JSON line to the alerts log file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "level": alert.level,
        "title": alert.title,
        "body": alert.body,
        "timestamp": alert.timestamp,
        "wallet": alert.wallet,
        "outcome_name": alert.outcome_name,
        "logged_at": datetime.now(tz=UTC).isoformat(),
    }
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def push_alert(alert: Alert, topic: str = "") -> None:
    """Send a push notification via ntfy.sh. Fire-and-forget."""
    topic = topic or NTFY_TOPIC
    if not topic:
        return

    title = f"🚨 {alert.level.upper()}: {alert.outcome_name}"
    body = f"{alert.body}\nWallet: {alert.wallet[:10]}..."
    priority = "urgent" if alert.level == "critical" else "high"

    req = Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode(),
        headers={
            "Title": title,
            "Priority": priority,
            "Tags": "warning",
        },
    )
    try:
        with urlopen(req, timeout=5):
            pass
    except (URLError, TimeoutError, OSError):
        logger.debug("Failed to send ntfy push")

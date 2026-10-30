"""Monitor state: dataclasses and persistence."""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TradeEvent:
    trade_id: str
    market_id: str
    token_id: str
    token_label: str
    side: str
    price: float
    size: float
    timestamp: int  # seconds
    user_address: str
    taker_address: str
    tx_hash: str
    block_number: int
    outcome_name: str = ""


@dataclass
class ClobTradeEvent:
    """Trade from CLOB WebSocket — fast but no wallet address."""

    token_id: str
    price: float
    size: float
    side: str  # "BUY" or "SELL"
    timestamp: int  # milliseconds from CLOB
    fee_rate_bps: str = "0"


@dataclass
class OrderbookSnapshot:
    token_id: str
    timestamp: int  # milliseconds
    bids: list[list[float]]  # [[price, size], ...]
    asks: list[list[float]]


@dataclass
class WalletProfile:
    eoa: str
    proxy: str
    handle: str
    pseudonym: str
    total_volume: float
    total_trades: int
    total_markets: int
    fetched_at: str = ""

    def to_dict(self) -> dict:
        return {
            "eoa": self.eoa,
            "proxy": self.proxy,
            "handle": self.handle,
            "pseudonym": self.pseudonym,
            "total_volume": self.total_volume,
            "total_trades": self.total_trades,
            "total_markets": self.total_markets,
            "fetched_at": self.fetched_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WalletProfile":
        return cls(**d)


@dataclass
class WalletSuspicion:
    address: str
    score: float
    reasons: list[str]
    outcome_name: str
    timestamp: str


@dataclass
class Alert:
    level: str  # "info", "warning", "critical"
    title: str
    body: str
    timestamp: str
    wallet: str = ""
    outcome_name: str = ""


@dataclass
class MonitorState:
    last_trade_ts: dict[str, int] = field(default_factory=dict)  # token_id -> epoch seconds
    seen_trade_ids: set[str] = field(default_factory=set)
    wallet_profiles: dict[str, WalletProfile] = field(default_factory=dict)  # address -> profile
    wallet_positions: dict[str, dict[str, float]] = field(default_factory=dict)
    latest_books: dict[str, OrderbookSnapshot] = field(default_factory=dict)
    current_prices: dict[str, float] = field(default_factory=dict)
    trade_windows: dict[str, list[TradeEvent]] = field(default_factory=dict)
    alerts: list[Alert] = field(default_factory=list)
    watchlist: dict[str, WalletSuspicion] = field(default_factory=dict)  # address -> suspicion

    def save_state(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "last_trade_ts": self.last_trade_ts,
            "seen_trade_ids": list(self.seen_trade_ids),
            "wallet_profiles": {k: v.to_dict() for k, v in self.wallet_profiles.items()},
            "wallet_positions": self.wallet_positions,
            "current_prices": self.current_prices,
            "alerts": [
                {
                    "level": a.level,
                    "title": a.title,
                    "body": a.body,
                    "timestamp": a.timestamp,
                    "wallet": a.wallet,
                    "outcome_name": a.outcome_name,
                }
                for a in self.alerts
            ],
            "watchlist": {
                k: {
                    "address": v.address,
                    "score": v.score,
                    "reasons": v.reasons,
                    "outcome_name": v.outcome_name,
                    "timestamp": v.timestamp,
                }
                for k, v in self.watchlist.items()
            },
            "saved_at": datetime.now(tz=UTC).isoformat(),
        }
        path.write_text(json.dumps(data, indent=2))
        logger.info("State saved to %s", path)

    @classmethod
    def load_state(cls, path: Path) -> "MonitorState":
        if not path.exists():
            logger.info("No saved state at %s, starting fresh", path)
            return cls()
        raw = json.loads(path.read_text())
        state = cls()
        state.last_trade_ts = raw.get("last_trade_ts", {})
        state.seen_trade_ids = set(raw.get("seen_trade_ids", []))
        state.wallet_profiles = {
            k: WalletProfile.from_dict(v) for k, v in raw.get("wallet_profiles", {}).items()
        }
        state.wallet_positions = raw.get("wallet_positions", {})
        state.current_prices = raw.get("current_prices", {})
        state.alerts = [Alert(**a) for a in raw.get("alerts", [])]
        state.watchlist = {k: WalletSuspicion(**v) for k, v in raw.get("watchlist", {}).items()}
        logger.info("State loaded from %s (%d seen trades)", path, len(state.seen_trade_ids))
        return state

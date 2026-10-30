"""Load and fetch event configuration for monitoring."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from papermarkets.ingest.client import public_client
from papermarkets.ingest.config import GAMMA_BASE_URL

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_EVENT_PATH = _ROOT / "data" / "monitor" / "zachxbt_event.json"


@dataclass(frozen=True)
class Outcome:
    name: str
    condition_id: str
    slug: str
    yes_token_id: str
    no_token_id: str
    yes_price: float | None
    volume: float
    active: bool


@dataclass(frozen=True)
class EventConfig:
    event_slug: str
    event_title: str
    neg_risk: bool
    neg_risk_market_id: str
    end_date: str
    created_at: str
    total_volume: float
    outcomes: list[Outcome]

    @property
    def active_outcomes(self) -> list[Outcome]:
        """Only outcomes with volume > 0."""
        return [o for o in self.outcomes if o.volume > 0]


def _parse_event(raw: dict) -> EventConfig:
    outcomes = [
        Outcome(
            name=o["name"],
            condition_id=o["condition_id"],
            slug=o["slug"],
            yes_token_id=o["yes_token_id"],
            no_token_id=o["no_token_id"],
            yes_price=o.get("yes_price"),
            volume=o.get("volume", 0),
            active=o.get("active", False),
        )
        for o in raw["outcomes"]
    ]
    return EventConfig(
        event_slug=raw["event_slug"],
        event_title=raw["event_title"],
        neg_risk=raw["neg_risk"],
        neg_risk_market_id=raw["neg_risk_market_id"],
        end_date=raw["end_date"],
        created_at=raw["created_at"],
        total_volume=raw["total_volume"],
        outcomes=outcomes,
    )


def load_event_config(path: Path | None = None) -> EventConfig:
    """Load event config from a saved JSON file."""
    p = path or _DEFAULT_EVENT_PATH
    raw = json.loads(p.read_text())
    return _parse_event(raw)


def fetch_event_config(event_slug: str) -> EventConfig:
    """Fetch fresh event config from the Gamma API."""
    url = f"{GAMMA_BASE_URL}/events?slug={event_slug}"
    data = public_client.get(url)
    # Gamma returns a list; take the first match
    if isinstance(data, list):
        if not data:
            raise ValueError(f"No event found for slug: {event_slug}")
        data = data[0]
    # Gamma uses "markets" instead of "outcomes", reshape
    markets = data.get("markets", [])
    outcomes_raw = []
    for m in markets:
        tokens = m.get("clobTokenIds", "").split(",") if m.get("clobTokenIds") else ["", ""]
        outcomes_raw.append(
            {
                "name": m.get("groupItemTitle", m.get("question", "")),
                "condition_id": m.get("conditionId", ""),
                "slug": m.get("slug", ""),
                "yes_token_id": tokens[0] if len(tokens) > 0 else "",
                "no_token_id": tokens[1] if len(tokens) > 1 else "",
                "yes_price": (
                    float(m["outcomePrices"].split(",")[0]) if m.get("outcomePrices") else None
                ),
                "volume": float(m.get("volume", 0)),
                "active": m.get("active", False),
            }
        )
    raw = {
        "event_slug": data.get("slug", event_slug),
        "event_title": data.get("title", ""),
        "neg_risk": data.get("negRisk", False),
        "neg_risk_market_id": data.get("negRiskMarketID", ""),
        "end_date": data.get("endDate", ""),
        "created_at": data.get("createdAt", ""),
        "total_volume": sum(o["volume"] for o in outcomes_raw),
        "outcomes": outcomes_raw,
    }
    return _parse_event(raw)
